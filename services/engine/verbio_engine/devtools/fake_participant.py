"""Fake LiveKit participant — publishes a WAV file as a mic track.

Used by the L6 Playwright E2E (apps/web/tests/e2e/) to populate a
session with deterministic audio without a real human in the loop.
The brief calls for "2 fake participants (pre-recorded audio loops)";
this script is one of those — the E2E runs two copies in parallel
with different identities + WAV files.

Usage:
    uv run verbio-fake-participant \\
        --livekit-url ws://localhost:7880 \\
        --api-key devkey --api-secret devsecret_long_enough_... \\
        --room room-abc --identity p-1 --display-name "Maya" \\
        --wav apps/web/tests/e2e/fixtures/participant-a.wav \\
        --duration 12

The script joins the room, publishes the WAV at its native sample
rate (10ms frames, mono), and exits once the WAV has been streamed
through `--duration` seconds (looping if shorter). The audio capture
agent inside the engine subscribes to the published track exactly as
it would for a real participant.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import wave
from collections.abc import Sequence
from pathlib import Path

import structlog
from livekit import api, rtc

log = structlog.get_logger(__name__)


_FRAME_MS = 10
"""LiveKit's audio source expects ~10ms frames for smooth pacing."""

_WAV_SAMPLE_WIDTH_BYTES = 2
"""16-bit PCM = 2 bytes per sample. The fixture pipeline targets this
explicitly; rejecting other widths keeps the test bed deterministic."""


async def _publish_audio(
    *,
    livekit_url: str,
    token: str,
    wav_path: Path,
    duration: float,
) -> None:
    room = rtc.Room()
    await room.connect(livekit_url, token, options=rtc.RoomOptions(auto_subscribe=False))
    log.info("fake_participant.connected", room=room.name)

    try:
        with wave.open(str(wav_path), "rb") as wf:
            sample_rate = wf.getframerate()
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            if sample_width != _WAV_SAMPLE_WIDTH_BYTES:
                msg = f"WAV must be 16-bit PCM; got {sample_width * 8}-bit"
                raise ValueError(msg)
            if channels not in (1, 2):
                msg = f"WAV must be mono or stereo; got {channels} channels"
                raise ValueError(msg)
            pcm = wf.readframes(wf.getnframes())

        source = rtc.AudioSource(sample_rate, channels)
        track = rtc.LocalAudioTrack.create_audio_track("mic", source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await room.local_participant.publish_track(track, options)
        log.info("fake_participant.published_track", sample_rate=sample_rate, channels=channels)

        # Send the WAV in 10ms slices, looping until `duration` seconds
        # have elapsed. Real participants stream continuously; looping a
        # short clip is the cheapest way to keep utterances flowing.
        samples_per_frame = (sample_rate * _FRAME_MS) // 1000
        bytes_per_frame = samples_per_frame * channels * sample_width
        loop = asyncio.get_running_loop()
        deadline = loop.time() + duration
        cursor = 0

        while loop.time() < deadline:
            if cursor + bytes_per_frame > len(pcm):
                cursor = 0  # loop back to the start of the clip
            chunk = pcm[cursor : cursor + bytes_per_frame]
            cursor += bytes_per_frame

            frame = rtc.AudioFrame(
                data=chunk,
                sample_rate=sample_rate,
                num_channels=channels,
                samples_per_channel=samples_per_frame,
            )
            await source.capture_frame(frame)
            # Pace at real time — capture_frame would buffer otherwise.
            await asyncio.sleep(_FRAME_MS / 1000)

        log.info("fake_participant.finished_publishing")
    finally:
        await room.disconnect()


def _mint_token(*, api_key: str, api_secret: str, room: str, identity: str, name: str) -> str:
    """Mint a join token for the fake participant.

    Mirrors `verbio-web`'s `lib/livekit-server.ts` — grants room-join
    + publish, no subscribe (the engine moderator subscribes; this
    script just produces audio).
    """
    grants = api.VideoGrants(
        room_join=True,
        room=room,
        can_publish=True,
        can_subscribe=False,
        can_publish_data=False,
    )
    return (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(name)
        .with_grants(grants)
        .to_jwt()
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verbio-fake-participant",
        description="Publish a WAV file to a LiveKit room as a fake participant.",
    )
    parser.add_argument("--livekit-url", required=True, help="ws:// or wss:// URL.")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--api-secret", required=True)
    parser.add_argument("--room", required=True, help="LiveKit room name.")
    parser.add_argument("--identity", required=True, help="LiveKit participant identity.")
    parser.add_argument("--display-name", required=True, help="Display name shown in the room.")
    parser.add_argument("--wav", required=True, type=Path, help="Path to a 16-bit PCM WAV file.")
    parser.add_argument(
        "--duration",
        type=float,
        default=12.0,
        help="Seconds of audio to publish (loops the WAV).",
    )
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    if not args.wav.exists():
        log.error("fake_participant.wav_missing", path=str(args.wav))
        return 1

    token = _mint_token(
        api_key=args.api_key,
        api_secret=args.api_secret,
        room=args.room,
        identity=args.identity,
        name=args.display_name,
    )

    try:
        await _publish_audio(
            livekit_url=args.livekit_url,
            token=token,
            wav_path=args.wav,
            duration=args.duration,
        )
    except Exception as exc:
        log.exception("fake_participant.failed", error=str(exc))
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        # Operator Ctrl-C — clean exit so the surrounding test harness
        # doesn't treat it as a failure if it was intentional.
        with contextlib.suppress(Exception):
            log.info("fake_participant.interrupted")
        return 0


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint.
    raise SystemExit(main(sys.argv[1:]))
