"""`ModeratorAudioPublisher` — pushes PCM `AudioChunk`s onto a LiveKit track.

This is the bridge between the TTS layer and the SFU. The moderator
joins each room as a participant (Phase 1) and Phase 4 §9 now lets
that participant publish audio: every spoken decision is rendered to
PCM by `CartesiaTTS` / `ElevenLabsTTS` and streamed here as
`AudioChunk`s, which we wrap into `rtc.AudioFrame`s and capture onto
an `rtc.AudioSource` published as a `LocalAudioTrack`.

A thin `AudioPublishBackend` Protocol fronts every LiveKit primitive
the publisher touches. Production builds wire it to the real
`livekit.rtc` SDK via `LiveKitAudioBackend`; tests pass a fake that
records every interaction without needing a live room. The publisher
itself stays SDK-agnostic, which is what makes it unit-testable.

Lifecycle invariants:
  * `start()` is idempotent — safe to call before every utterance, or
    once at session join.
  * `aclose()` is idempotent — safe to call from cleanup paths even
    when `start()` never ran.
  * `publish()` raises if called before `start()` — the explicit
    crash is preferable to silently dropping audio frames.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

import structlog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from verbio_engine.tts.protocol import AudioChunk

log = structlog.get_logger(__name__)

DEFAULT_TRACK_NAME = "moderator-voice"
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_NUM_CHANNELS = 1

# pcm_s16le = 2 bytes/sample/channel; samples_per_channel = bytes / (2 * channels)
_BYTES_PER_SAMPLE = 2


@runtime_checkable
class AudioFrameLike(Protocol):
    """Subset of `rtc.AudioFrame` the publisher constructs and pushes."""

    @property
    def data(self) -> bytes: ...
    @property
    def sample_rate(self) -> int: ...
    @property
    def num_channels(self) -> int: ...
    @property
    def samples_per_channel(self) -> int: ...


@runtime_checkable
class AudioSourceLike(Protocol):
    """Subset of `rtc.AudioSource` the publisher drives."""

    async def capture_frame(self, frame: AudioFrameLike) -> None: ...
    async def aclose(self) -> None: ...
    async def wait_for_playout(self) -> None: ...
    def clear_queue(self) -> None: ...


@runtime_checkable
class AudioPublishBackend(Protocol):
    """LiveKit-shaped surface the publisher needs.

    The split lets tests substitute a recording fake while the
    production wire-up in `LiveKitAudioBackend` is a one-liner per
    method that delegates to the rtc SDK.
    """

    def create_source(self, *, sample_rate: int, num_channels: int) -> AudioSourceLike: ...
    def make_frame(
        self,
        *,
        data: bytes,
        sample_rate: int,
        num_channels: int,
        samples_per_channel: int,
    ) -> AudioFrameLike: ...
    async def publish_source(self, *, source: AudioSourceLike, track_name: str) -> None: ...
    async def unpublish_source(self, *, source: AudioSourceLike) -> None: ...


class LiveKitAudioBackend:
    """Default backend wiring directly to `livekit.rtc`.

    Constructed once per agent runtime with the room's
    `local_participant`. The publisher stays a pure dependency-inversion
    consumer of this class, so any future SDK change lands in one
    place.
    """

    def __init__(self, local_participant: Any) -> None:
        # Typed as Any because `livekit.rtc.LocalParticipant` import
        # would pull the SDK at module-load time, which we want to
        # avoid for fast unit tests of the publisher.
        self._local_participant = local_participant
        self._tracks: dict[int, Any] = {}
        self._publications: dict[int, Any] = {}

    def create_source(self, *, sample_rate: int, num_channels: int) -> AudioSourceLike:
        from livekit import rtc

        # cast() because rtc.AudioSource.capture_frame() narrows its
        # frame arg to the concrete rtc.AudioFrame, which makes it not
        # structurally a subtype of our AudioSourceLike (which accepts
        # any AudioFrameLike). Runtime behaviour matches; we lean on
        # the cast to satisfy mypy without weakening the Protocol used
        # by tests.
        return cast("AudioSourceLike", rtc.AudioSource(sample_rate, num_channels))

    def make_frame(
        self,
        *,
        data: bytes,
        sample_rate: int,
        num_channels: int,
        samples_per_channel: int,
    ) -> AudioFrameLike:
        from livekit import rtc

        # cast() because rtc.AudioFrame.data is a memoryview, not bytes.
        # The publisher never reads .data back, so the divergence is
        # purely a type signature mismatch.
        return cast(
            "AudioFrameLike",
            rtc.AudioFrame(
                data=data,
                sample_rate=sample_rate,
                num_channels=num_channels,
                samples_per_channel=samples_per_channel,
            ),
        )

    async def publish_source(self, *, source: AudioSourceLike, track_name: str) -> None:
        from livekit import rtc

        track = rtc.LocalAudioTrack.create_audio_track(track_name, cast("Any", source))
        opts = rtc.TrackPublishOptions()
        opts.source = rtc.TrackSource.SOURCE_MICROPHONE
        publication = await self._local_participant.publish_track(track, opts)
        # Hold strong references so the C-side track isn't GC'd while
        # capture_frame() is still being driven from the publisher.
        self._tracks[id(source)] = track
        self._publications[id(source)] = publication

    async def unpublish_source(self, *, source: AudioSourceLike) -> None:
        publication = self._publications.pop(id(source), None)
        self._tracks.pop(id(source), None)
        if publication is not None:
            # Some SDK versions expose `unpublish_track(sid)`, others
            # auto-unpublish when the track is closed. Try the
            # explicit path first; closing the source then drains both.
            sid = getattr(publication, "sid", None)
            unpub = getattr(self._local_participant, "unpublish_track", None)
            if sid is not None and unpub is not None:
                await unpub(sid)


class ModeratorAudioPublisher:
    """Lifecycle wrapper that turns an `AudioChunk` stream into LiveKit frames."""

    def __init__(
        self,
        *,
        backend: AudioPublishBackend,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        num_channels: int = DEFAULT_NUM_CHANNELS,
        track_name: str = DEFAULT_TRACK_NAME,
    ) -> None:
        if sample_rate <= 0:
            msg = f"sample_rate must be positive, got {sample_rate}"
            raise ValueError(msg)
        if num_channels <= 0:
            msg = f"num_channels must be positive, got {num_channels}"
            raise ValueError(msg)
        self._backend = backend
        self._sample_rate = sample_rate
        self._num_channels = num_channels
        self._track_name = track_name
        self._source: AudioSourceLike | None = None

    @property
    def is_started(self) -> bool:
        return self._source is not None

    async def start(self) -> None:
        """Create the LiveKit audio source + publish the track. Idempotent."""
        if self._source is not None:
            return
        source = self._backend.create_source(
            sample_rate=self._sample_rate,
            num_channels=self._num_channels,
        )
        await self._backend.publish_source(source=source, track_name=self._track_name)
        self._source = source
        log.info(
            "moderator_audio_track_published",
            track_name=self._track_name,
            sample_rate=self._sample_rate,
            num_channels=self._num_channels,
        )

    async def publish(self, chunks: AsyncIterator[AudioChunk]) -> int:
        """Drain `chunks` onto the LiveKit source. Returns frames pushed."""
        if self._source is None:
            msg = "ModeratorAudioPublisher.publish() called before start()"
            raise RuntimeError(msg)

        bytes_per_frame = _BYTES_PER_SAMPLE * self._num_channels
        frames_pushed = 0

        async for chunk in chunks:
            if chunk.is_final or not chunk.pcm:
                continue

            samples_per_channel = len(chunk.pcm) // bytes_per_frame
            if samples_per_channel == 0:
                continue
            # Drop any trailing partial-sample byte before constructing
            # the frame; LiveKit expects an exact (samples * bytes_per_frame)
            # buffer or the C side complains about misaligned audio.
            usable = samples_per_channel * bytes_per_frame
            frame = self._backend.make_frame(
                data=chunk.pcm[:usable],
                sample_rate=chunk.sample_rate,
                num_channels=self._num_channels,
                samples_per_channel=samples_per_channel,
            )
            await self._source.capture_frame(frame)
            frames_pushed += 1

        return frames_pushed

    async def wait_for_playout(self) -> None:
        """Block until the SFU has drained the buffered audio."""
        if self._source is not None:
            await self._source.wait_for_playout()

    def interrupt(self) -> None:
        """Drop any queued audio so the next utterance starts cleanly."""
        if self._source is not None:
            self._source.clear_queue()

    async def aclose(self) -> None:
        """Idempotent shutdown — unpublish + close the underlying source."""
        source = self._source
        if source is None:
            return
        self._source = None
        await self._backend.unpublish_source(source=source)
        await source.aclose()
