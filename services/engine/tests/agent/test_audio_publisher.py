"""`ModeratorAudioPublisher` — lifecycle + frame conversion + LiveKit handoff.

The publisher is unit-tested against an in-memory `_FakeBackend` that
records every call. This isolates the audio-frame math (PCM → samples-
per-channel → frame) and the start/publish/aclose lifecycle from the
LiveKit SDK, which can't be exercised without a live SFU room.

Backend coverage is kept narrow: each fake method records arguments
and returns deterministic stand-ins for `rtc.AudioSource` / `rtc.AudioFrame`.
The publisher itself stays the unit under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from verbio_engine.agent.audio_publisher import (
    DEFAULT_NUM_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_TRACK_NAME,
    AudioFrameLike,
    AudioSourceLike,
    ModeratorAudioPublisher,
)
from verbio_engine.tts.protocol import AudioChunk

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeFrame:
    """Stand-in for `rtc.AudioFrame` — records the four constructor args."""

    data: bytes
    sample_rate: int
    num_channels: int
    samples_per_channel: int


@dataclass
class _FakeAudioSource:
    """Stand-in for `rtc.AudioSource` — records capture / lifecycle calls."""

    captured: list[_FakeFrame] = field(default_factory=list)
    wait_calls: int = 0
    clear_calls: int = 0
    close_calls: int = 0

    async def capture_frame(self, frame: AudioFrameLike) -> None:
        assert isinstance(frame, _FakeFrame)
        self.captured.append(frame)

    async def aclose(self) -> None:
        self.close_calls += 1

    async def wait_for_playout(self) -> None:
        self.wait_calls += 1

    def clear_queue(self) -> None:
        self.clear_calls += 1


@dataclass
class _FakeBackend:
    """Recording fake of `AudioPublishBackend` — every method is observable."""

    sources: list[_FakeAudioSource] = field(default_factory=list)
    publish_calls: list[tuple[_FakeAudioSource, str]] = field(default_factory=list)
    unpublish_calls: list[_FakeAudioSource] = field(default_factory=list)

    def create_source(self, *, sample_rate: int, num_channels: int) -> AudioSourceLike:
        source = _FakeAudioSource()
        # Stash configuration on the fake so tests can assert what the
        # publisher requested without inspecting backend internals.
        source.sample_rate = sample_rate  # type: ignore[attr-defined]
        source.num_channels = num_channels  # type: ignore[attr-defined]
        self.sources.append(source)
        return source

    def make_frame(
        self,
        *,
        data: bytes,
        sample_rate: int,
        num_channels: int,
        samples_per_channel: int,
    ) -> AudioFrameLike:
        return _FakeFrame(
            data=data,
            sample_rate=sample_rate,
            num_channels=num_channels,
            samples_per_channel=samples_per_channel,
        )

    async def publish_source(self, *, source: AudioSourceLike, track_name: str) -> None:
        assert isinstance(source, _FakeAudioSource)
        self.publish_calls.append((source, track_name))

    async def unpublish_source(self, *, source: AudioSourceLike) -> None:
        assert isinstance(source, _FakeAudioSource)
        self.unpublish_calls.append(source)


async def _aiter(*chunks: AudioChunk) -> AsyncIterator[AudioChunk]:
    for chunk in chunks:
        yield chunk


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_uses_documented_defaults(self) -> None:
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)
        assert publisher.is_started is False
        # Defaults match the brief: 24 kHz mono, "moderator-voice" track.
        assert DEFAULT_SAMPLE_RATE == 24000
        assert DEFAULT_NUM_CHANNELS == 1
        assert DEFAULT_TRACK_NAME == "moderator-voice"

    def test_rejects_non_positive_sample_rate(self) -> None:
        with pytest.raises(ValueError, match="sample_rate must be positive"):
            ModeratorAudioPublisher(backend=_FakeBackend(), sample_rate=0)
        with pytest.raises(ValueError, match="sample_rate must be positive"):
            ModeratorAudioPublisher(backend=_FakeBackend(), sample_rate=-1)

    def test_rejects_non_positive_num_channels(self) -> None:
        with pytest.raises(ValueError, match="num_channels must be positive"):
            ModeratorAudioPublisher(backend=_FakeBackend(), num_channels=0)
        with pytest.raises(ValueError, match="num_channels must be positive"):
            ModeratorAudioPublisher(backend=_FakeBackend(), num_channels=-1)


# ---------------------------------------------------------------------------
# start()
# ---------------------------------------------------------------------------


class TestStart:
    async def test_creates_source_and_publishes_track(self) -> None:
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)

        await publisher.start()

        assert publisher.is_started is True
        # One source created with the publisher's sample-rate / channels.
        assert len(backend.sources) == 1
        source = backend.sources[0]
        assert source.sample_rate == DEFAULT_SAMPLE_RATE  # type: ignore[attr-defined]
        assert source.num_channels == DEFAULT_NUM_CHANNELS  # type: ignore[attr-defined]
        # Track published with the same source under the default name.
        assert backend.publish_calls == [(source, DEFAULT_TRACK_NAME)]

    async def test_start_is_idempotent(self) -> None:
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)

        await publisher.start()
        await publisher.start()
        await publisher.start()

        # Second / third start() are no-ops — exactly one source, one publish.
        assert len(backend.sources) == 1
        assert len(backend.publish_calls) == 1

    async def test_passes_custom_sample_rate_and_track_name(self) -> None:
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(
            backend=backend,
            sample_rate=16000,
            num_channels=2,
            track_name="custom-track",
        )

        await publisher.start()

        source = backend.sources[0]
        assert source.sample_rate == 16000  # type: ignore[attr-defined]
        assert source.num_channels == 2  # type: ignore[attr-defined]
        assert backend.publish_calls[0][1] == "custom-track"


# ---------------------------------------------------------------------------
# publish()
# ---------------------------------------------------------------------------


class TestPublish:
    async def test_raises_when_not_started(self) -> None:
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)

        with pytest.raises(RuntimeError, match="called before start"):
            await publisher.publish(_aiter(AudioChunk(pcm=b"\x00\x01", sample_rate=24000)))

    async def test_returns_frame_count_for_drained_stream(self) -> None:
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)
        await publisher.start()

        # Two non-terminator chunks → two frames.
        n = await publisher.publish(
            _aiter(
                AudioChunk(pcm=b"\x00\x01\x02\x03", sample_rate=24000),
                AudioChunk(pcm=b"\x04\x05\x06\x07", sample_rate=24000),
                AudioChunk(pcm=b"", sample_rate=24000, is_final=True),
            )
        )

        assert n == 2
        source = backend.sources[0]
        assert len(source.captured) == 2

    async def test_skips_terminator_chunk(self) -> None:
        # Terminator carries no payload — it must not become a frame
        # otherwise LiveKit gets a zero-sample frame and complains.
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)
        await publisher.start()

        n = await publisher.publish(_aiter(AudioChunk(pcm=b"", sample_rate=24000, is_final=True)))

        assert n == 0
        assert backend.sources[0].captured == []

    async def test_skips_empty_non_terminator_chunks(self) -> None:
        # Provider can emit empty mid-stream chunks (keepalives, parse
        # artifacts) — they're noise, not audio.
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)
        await publisher.start()

        n = await publisher.publish(
            _aiter(
                AudioChunk(pcm=b"", sample_rate=24000),
                AudioChunk(pcm=b"\x00\x01", sample_rate=24000),
                AudioChunk(pcm=b"", sample_rate=24000),
            )
        )

        assert n == 1
        assert len(backend.sources[0].captured) == 1

    async def test_computes_samples_per_channel_for_mono(self) -> None:
        # 8 bytes of pcm_s16le mono → 4 samples per channel.
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)
        await publisher.start()

        await publisher.publish(
            _aiter(AudioChunk(pcm=b"\x00\x01\x02\x03\x04\x05\x06\x07", sample_rate=24000))
        )

        frame = backend.sources[0].captured[0]
        assert frame.samples_per_channel == 4
        assert frame.num_channels == 1
        assert frame.sample_rate == 24000
        assert frame.data == b"\x00\x01\x02\x03\x04\x05\x06\x07"

    async def test_computes_samples_per_channel_for_stereo(self) -> None:
        # 8 bytes of pcm_s16le stereo → 2 samples per channel.
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend, num_channels=2)
        await publisher.start()

        await publisher.publish(
            _aiter(AudioChunk(pcm=b"\x00\x01\x02\x03\x04\x05\x06\x07", sample_rate=24000))
        )

        frame = backend.sources[0].captured[0]
        assert frame.samples_per_channel == 2
        assert frame.num_channels == 2

    async def test_truncates_trailing_partial_sample_bytes(self) -> None:
        # 5 bytes mono = 2 complete samples + 1 stray byte. The stray
        # byte must be dropped so LiveKit gets a (samples * bytes/frame)
        # aligned buffer — otherwise the C side rejects it.
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)
        await publisher.start()

        await publisher.publish(_aiter(AudioChunk(pcm=b"\x00\x01\x02\x03\x04", sample_rate=24000)))

        frame = backend.sources[0].captured[0]
        assert frame.samples_per_channel == 2
        assert frame.data == b"\x00\x01\x02\x03"

    async def test_drops_chunks_smaller_than_one_full_sample(self) -> None:
        # A single byte of mono pcm_s16le is half a sample — no frame
        # at all, not a zero-sample frame.
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)
        await publisher.start()

        n = await publisher.publish(_aiter(AudioChunk(pcm=b"\x00", sample_rate=24000)))

        assert n == 0
        assert backend.sources[0].captured == []

    async def test_uses_chunk_sample_rate_not_publisher_default(self) -> None:
        # The provider can announce a different rate per chunk; the
        # publisher passes it through so LiveKit resamples correctly.
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)
        await publisher.start()

        await publisher.publish(_aiter(AudioChunk(pcm=b"\x00\x01", sample_rate=16000)))

        assert backend.sources[0].captured[0].sample_rate == 16000


# ---------------------------------------------------------------------------
# wait_for_playout() / interrupt()
# ---------------------------------------------------------------------------


class TestPlayoutAndInterrupt:
    async def test_wait_for_playout_delegates_to_source(self) -> None:
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)
        await publisher.start()

        await publisher.wait_for_playout()
        await publisher.wait_for_playout()

        assert backend.sources[0].wait_calls == 2

    async def test_wait_for_playout_is_safe_before_start(self) -> None:
        # Called from cleanup paths even if start() never ran — no crash.
        publisher = ModeratorAudioPublisher(backend=_FakeBackend())
        await publisher.wait_for_playout()

    async def test_interrupt_clears_queued_audio(self) -> None:
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)
        await publisher.start()

        publisher.interrupt()
        publisher.interrupt()

        assert backend.sources[0].clear_calls == 2

    async def test_interrupt_is_safe_before_start(self) -> None:
        publisher = ModeratorAudioPublisher(backend=_FakeBackend())
        publisher.interrupt()  # no-op, must not raise


# ---------------------------------------------------------------------------
# aclose()
# ---------------------------------------------------------------------------


class TestAclose:
    async def test_unpublishes_then_closes_source(self) -> None:
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)
        await publisher.start()
        source = backend.sources[0]

        await publisher.aclose()

        assert publisher.is_started is False
        assert backend.unpublish_calls == [source]
        assert source.close_calls == 1

    async def test_aclose_is_idempotent(self) -> None:
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)
        await publisher.start()

        await publisher.aclose()
        await publisher.aclose()
        await publisher.aclose()

        # Second / third aclose() are no-ops.
        assert len(backend.unpublish_calls) == 1
        assert backend.sources[0].close_calls == 1

    async def test_aclose_is_safe_before_start(self) -> None:
        # Cleanup paths must tolerate a publisher that never started.
        publisher = ModeratorAudioPublisher(backend=_FakeBackend())
        await publisher.aclose()
        assert publisher.is_started is False

    async def test_publish_after_aclose_raises(self) -> None:
        # Once shut down, the contract is the same as never-started:
        # publish() crashes loudly instead of silently dropping audio.
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)
        await publisher.start()
        await publisher.aclose()

        with pytest.raises(RuntimeError, match="called before start"):
            await publisher.publish(_aiter(AudioChunk(pcm=b"\x00\x01", sample_rate=24000)))

    async def test_can_restart_after_aclose(self) -> None:
        # start() → aclose() → start() should give a fresh source +
        # republished track, not silently reuse the dead source.
        backend = _FakeBackend()
        publisher = ModeratorAudioPublisher(backend=backend)

        await publisher.start()
        await publisher.aclose()
        await publisher.start()

        assert len(backend.sources) == 2
        assert len(backend.publish_calls) == 2
        assert publisher.is_started is True
