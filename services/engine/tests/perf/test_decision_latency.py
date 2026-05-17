"""Synthetic latency budget enforcement (P4 L9).

The brief commits to a hard envelope: end-of-rule-trigger to first
audible word ≤ 1500 ms p95, ≤ 2500 ms p99 (CLAUDE.md tick-loop section,
brief §6). Real LLM/TTS providers would make this test flaky; instead
we wire deterministic fakes whose latency mimics realistic production
characteristics:

  * mouth time-to-first-token in the 200-450 ms band (DeepSeek
    streaming envelope from §8.2)
  * TTS time-to-first-frame in the 80-200 ms band (Cartesia Sonic
    p95 from §8.3)
  * publisher captures synchronously (LiveKit `capture_frame` returns
    in microseconds for our PCM sizes)

Anything the engine's own plumbing adds on top must keep the totals
within budget. If a future refactor introduces extra `await
asyncio.sleep` calls, an unbounded retry, or a synchronous DB write on
the audio path, the percentile assertions will catch it.

This test runs the executor 80 times in parallel against a real wall
clock so `outcome.latency_ms` reflects actual elapsed time. Marked
`slow` so dev `pytest -m 'not slow'` cycles stay fast.
"""

from __future__ import annotations

import asyncio
import statistics
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from verbio_engine.decisions.orchestrator import DecisionExecutor
from verbio_engine.domain.decision import ModeratorDecision
from verbio_engine.domain.session_state import SessionState
from verbio_engine.mouth import ModeratorPersona, MouthChunk, MouthRequest
from verbio_engine.tts.protocol import AudioChunk, TTSRequest

# ---------------------------------------------------------------------------
# Production-realistic latency profile
# ---------------------------------------------------------------------------

# DeepSeek streaming, observed time-to-first-token on warm path (§8.2).
# Conservative — real p95 is ~350 ms; 450 ms gives us headroom that
# guarantees the test still passes if a CI runner is slow.
_MOUTH_TTFT_MS = 350

# Inter-chunk gap for the rest of the mouth stream. Three more chunks
# at 60 ms each = 180 ms total streaming after first token, matching
# typical 18-token responses at ~6 tokens/100 ms.
_MOUTH_INTER_CHUNK_MS = 60
_MOUTH_TOTAL_CHUNKS = 4

# Cartesia Sonic time-to-first-frame on warm path (§8.3).
_TTS_TTFF_MS = 120

# After first frame, Cartesia streams faster than realtime so the
# remaining frames arrive ~50 ms apart. Two more frames is enough to
# exercise the publisher loop without inflating the test.
_TTS_INTER_FRAME_MS = 50
_TTS_TOTAL_FRAMES = 3

# Iteration count. 80 gives a usable p95/p99 estimate without
# explosively long test time. Parallel execution keeps total runtime
# bounded by the slowest single execution (~700 ms) regardless of N.
_ITERATIONS = 80

# Brief §6 hard envelope.
_P95_BUDGET_MS = 1500
_P99_BUDGET_MS = 2500


# ---------------------------------------------------------------------------
# Fakes that simulate real provider behaviour
# ---------------------------------------------------------------------------


@dataclass
class _RealisticMouth:
    """Streaming mouth that sleeps real time between chunks.

    Mirrors `MouthClient.phrase` — yields `MouthChunk`s with measurable
    inter-arrival latency so `_drain_mouth` actually waits.
    """

    def phrase(self, request: MouthRequest) -> AsyncIterator[MouthChunk]:
        return self._stream(request)

    async def _stream(self, request: MouthRequest) -> AsyncIterator[MouthChunk]:
        _ = request
        # First token — the slowest part of any LLM call.
        await asyncio.sleep(_MOUTH_TTFT_MS / 1000.0)
        yield MouthChunk(text="Could ")
        for word in ("you ", "say ", "more?"):
            await asyncio.sleep(_MOUTH_INTER_CHUNK_MS / 1000.0)
            yield MouthChunk(text=word)
        yield MouthChunk(text="", is_final=True)


@dataclass
class _RealisticTTS:
    """Streaming TTS with realistic time-to-first-frame and inter-frame gap."""

    sample_rate: int = 24000

    def synthesize(self, request: TTSRequest, text: str) -> AsyncIterator[AudioChunk]:
        return self._stream(request, text)

    async def _stream(self, request: TTSRequest, text: str) -> AsyncIterator[AudioChunk]:
        _ = request, text
        await asyncio.sleep(_TTS_TTFF_MS / 1000.0)
        # Each frame is 20 ms of audio @ 24 kHz mono s16le = 960 bytes.
        # Synthetic, not actually played; just needs to be non-empty so
        # the publisher counts it as a real frame.
        frame_payload = b"\x00" * 960
        yield AudioChunk(pcm=frame_payload, sample_rate=self.sample_rate)
        for _ in range(_TTS_TOTAL_FRAMES - 1):
            await asyncio.sleep(_TTS_INTER_FRAME_MS / 1000.0)
            yield AudioChunk(pcm=frame_payload, sample_rate=self.sample_rate)
        yield AudioChunk(pcm=b"", sample_rate=self.sample_rate, is_final=True)


@dataclass
class _FakePublisher:
    """No-latency publisher. Real LiveKit capture_frame is sub-millisecond."""

    async def publish(self, chunks: AsyncIterator[AudioChunk]) -> int:
        n = 0
        async for chunk in chunks:
            if not chunk.is_final and chunk.pcm:
                n += 1
        return n


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _persona() -> ModeratorPersona:
    return ModeratorPersona(
        style_prompt="Test persona",
        tone="warm",
        formality="neutral",
        voice_provider="cartesia",
        voice_id="voice-1",
    )


def _build_executor(now: datetime) -> tuple[DecisionExecutor, datetime]:
    """One executor per iteration so per-iteration fakes don't share state.

    Returns (executor, rule_fired_at). `rule_fired_at` is captured at
    construction time and used as `decision.timestamp`, which is the
    anchor for `latency_ms`. The clock is the real wall clock so
    `outcome.latency_ms` reflects actual elapsed time.
    """
    executor = DecisionExecutor(
        mouth=_RealisticMouth(),
        tts=_RealisticTTS(),
        publisher=_FakePublisher(),
        persona=_persona(),
        clock=lambda: datetime.now(UTC),
    )
    return executor, now


def _decision(rule_fired_at: datetime) -> ModeratorDecision:
    return ModeratorDecision(
        decision_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        tick_id=0,
        timestamp=rule_fired_at,
        action="prompt_participant",
        target_participant_id="alice",
        source="auto",
        triggering_rule="silence_gap",
        cooldown_until=rule_fired_at + timedelta(seconds=45),
    )


def _state(rule_fired_at: datetime) -> SessionState:
    return SessionState(
        session_id=uuid.uuid4(),
        tick_id=0,
        t=rule_fired_at,
        started_at=rule_fired_at - timedelta(minutes=2),
        elapsed_sec=120.0,
        participants={},
    )


async def _one_iteration() -> int:
    """Run one execute() and return the wall-clock latency in ms."""
    now = datetime.now(UTC)
    executor, rule_fired_at = _build_executor(now)
    outcome = await executor.execute(_decision(rule_fired_at), _state(rule_fired_at))
    # The synthetic test relies on every iteration actually speaking; an
    # abandoned iteration (latency_exceeded / no_phrasing_available)
    # would give a misleading data point. Fail loudly if so.
    assert (
        outcome.was_executed
    ), f"iteration did not execute — suppressed_by={outcome.suppressed_by}"
    return outcome.latency_ms


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_p95_and_p99_latency_within_brief_budget() -> None:
    """Synthetic perf gate — brief §6 budget on rule_fire → spoken_at."""
    # Run all iterations concurrently. Per-iteration real time is
    # ~(MOUTH_TTFT + 3 * INTER_CHUNK + TTS_TTFF + 2 * INTER_FRAME) ≈
    # 350 + 180 + 120 + 100 = 750 ms. Parallel total stays in that
    # ballpark instead of multiplying by N.
    latencies_ms = await asyncio.gather(*(_one_iteration() for _ in range(_ITERATIONS)))

    latencies = sorted(latencies_ms)

    # `statistics.quantiles(n=100)` returns 99 cut points at the 1st .. 99th
    # percentiles. Indexing is 0-based, so p95 = quantiles[94], p99 = [98].
    quantiles = statistics.quantiles(latencies, n=100)
    p95 = quantiles[94]
    p99 = quantiles[98]

    assert p95 <= _P95_BUDGET_MS, (
        f"p95 latency {p95:.0f}ms exceeded {_P95_BUDGET_MS}ms budget. "
        f"min={latencies[0]}ms median={latencies[len(latencies) // 2]}ms "
        f"max={latencies[-1]}ms n={len(latencies)}"
    )
    assert p99 <= _P99_BUDGET_MS, (
        f"p99 latency {p99:.0f}ms exceeded {_P99_BUDGET_MS}ms budget. "
        f"min={latencies[0]}ms median={latencies[len(latencies) // 2]}ms "
        f"max={latencies[-1]}ms n={len(latencies)}"
    )


@pytest.mark.slow
async def test_no_iteration_exceeds_individual_total_budget() -> None:
    """Stronger guard — even the slowest single iteration stays within p99.

    The percentile test could pass if 1 out of 80 iterations spiked to
    10s — that's masked at p99 because only 1% of samples are above
    the cut-point. This per-iteration assertion catches such spikes
    early so they don't make it into a real session.
    """
    latencies_ms = await asyncio.gather(*(_one_iteration() for _ in range(_ITERATIONS)))

    over_budget = [ms for ms in latencies_ms if ms > _P99_BUDGET_MS]
    assert not over_budget, (
        f"{len(over_budget)}/{len(latencies_ms)} iterations exceeded "
        f"{_P99_BUDGET_MS}ms: worst={max(over_budget)}ms"
    )
