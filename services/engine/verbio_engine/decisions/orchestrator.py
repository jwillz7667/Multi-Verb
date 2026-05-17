"""`DecisionExecutor` — bridges a persisted decision to actual audio (P4 L8, P5 L4).

The tick loop's job ends at the audit row (`DecisionTickListener`). This
module owns the *execution* step — phrasing via the mouth layer,
synthesising via TTS, capturing PCM onto the LiveKit moderator track —
and the latency invariants from brief §6 + §8.4.

Three brief commitments are encoded in here, not at the call site:

  * §8.4 mouth budget. The mouth call is wrapped in an 800 ms wall-clock
    deadline; on timeout or error we fall through to the templated
    phrasing. The `from_fallback` outcome flag lets the dispatcher mark
    `llm_fallback` on the row.

  * §9 cached fallback. When the templated path is taken AND the persona
    has a `FallbackPhraseCache` entry for the action, the cached PCM is
    published verbatim, skipping TTS entirely — the §9 "nearly instant"
    path that protects the §6 1500 ms p95 budget when the mouth blows.

  * §6 latency guard. Before pushing any audio, we re-check wall-clock
    elapsed since `decision.timestamp` (rule_fired_at). If the total
    budget is already burnt, we abandon execution with
    `suppressed_by=['latency_exceeded']` and `was_executed=False` —
    exactly the audit-trail outcome the brief mandates.

Whisper path (P5 L4). When `decision.source == "researcher_whisper"`,
the mouth layer is bypassed entirely: the researcher's verbatim text
(carried on `decision.researcher_hint`) goes straight to TTS. The
mouth budget, fallback templates, and cached-phrase shortcut do not
apply — there's no LLM in the loop and no per-action template to fall
back to. The §6 total latency guard still runs so a backlogged tick
doesn't speak stale words; the audit row stays the truth.

The dispatcher (`verbio_engine.decisions.dispatcher.ExecutionDispatcher`)
runs the executor as a background `asyncio.Task` so the tick loop never
blocks waiting on mouth/TTS — that's the §6 step-6 invariant from a
control-flow angle.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from verbio_engine.logging import get_logger
from verbio_engine.mouth import (
    MouthRequest,
    NoFallbackTemplateError,
    extract_phrasing_context,
    format_template,
)
from verbio_engine.tts.protocol import AudioChunk, TTSError, TTSRequest

if TYPE_CHECKING:
    from datetime import datetime

    from verbio_engine.domain.decision import DecisionAction, ModeratorDecision
    from verbio_engine.domain.session_state import SessionState
    from verbio_engine.mouth import (
        FallbackPhraseCache,
        ModeratorPersona,
        MouthClient,
    )
    from verbio_engine.tts.protocol import TTSClient


class AudioPublisher(Protocol):
    """Narrow contract the executor needs from `ModeratorAudioPublisher`.

    A Protocol (rather than the concrete class) keeps the executor
    decoupled from LiveKit specifics and lets tests substitute a simple
    capture-into-list fake without inheritance or casts. The production
    `ModeratorAudioPublisher.publish` already satisfies the signature.
    """

    async def publish(self, chunks: AsyncIterator[AudioChunk]) -> int: ...


log = get_logger(__name__)

ClockFn = Callable[[], "datetime"]
"""Callable returning the current wall-clock time. Injected for testability."""

DEFAULT_MOUTH_BUDGET_MS = 800
"""§8.4: LLM call has 800 ms wall clock before we fall back to templates."""

DEFAULT_TOTAL_BUDGET_MS = 1500
"""§6 p95 target: rule fire → first audible word ≤ 1500 ms."""


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """Result of one `DecisionExecutor.execute()` call.

    The dispatcher persists this into the existing `decisions` row via
    `DecisionRepo.update_execution`. `suppressed_by` is *additive* — it
    extends whatever codes the resolver already wrote (cooldown,
    quietness_budget, lower_priority_won) with execution-time codes
    (mouth_timeout, mouth_failed, tts_failed, latency_exceeded,
    llm_fallback).
    """

    was_executed: bool
    llm_output: str | None
    spoken_at: datetime | None
    suppressed_by: list[str] = field(default_factory=list)
    latency_ms: int = 0
    """Wall clock from `decision.timestamp` to first frame pushed.

    When `was_executed` is False the value still reflects how long the
    pipeline ran before giving up — useful for distinguishing "burnt the
    budget on the mouth" from "TTS errored instantly".
    """


class DecisionExecutor:
    """Runs one decision through mouth → TTS → publisher with the §6/§8.4 budgets."""

    __slots__ = (
        "_clock",
        "_fallback_cache",
        "_mouth",
        "_mouth_budget_sec",
        "_persona",
        "_publisher",
        "_total_budget_sec",
        "_tts",
    )

    def __init__(
        self,
        *,
        mouth: MouthClient,
        tts: TTSClient,
        publisher: AudioPublisher,
        persona: ModeratorPersona,
        clock: ClockFn,
        fallback_cache: FallbackPhraseCache | None = None,
        mouth_budget_ms: int = DEFAULT_MOUTH_BUDGET_MS,
        total_budget_ms: int = DEFAULT_TOTAL_BUDGET_MS,
    ) -> None:
        if mouth_budget_ms <= 0:
            msg = f"mouth_budget_ms must be positive, got {mouth_budget_ms}"
            raise ValueError(msg)
        if total_budget_ms <= 0:
            msg = f"total_budget_ms must be positive, got {total_budget_ms}"
            raise ValueError(msg)
        self._mouth = mouth
        self._tts = tts
        self._publisher = publisher
        self._persona = persona
        self._clock = clock
        self._fallback_cache = fallback_cache
        self._mouth_budget_sec = mouth_budget_ms / 1000.0
        self._total_budget_sec = total_budget_ms / 1000.0

    async def execute(  # noqa: PLR0911 — each return is a distinct audit outcome
        self,
        decision: ModeratorDecision,
        state: SessionState,
    ) -> ExecutionOutcome:
        """Phrase + speak `decision`. Returns the post-execution outcome.

        The function is intentionally a small state machine with one
        return per audit-distinguishable outcome (whisper_no_text,
        no_phrasing_available, latency_exceeded, cached fallback,
        tts_failed, tts_no_audio, success). Merging branches would
        obscure which code path produced which audit row — those rows
        are the brief's "why didn't it speak?" guarantee, so the
        explicit returns stay.
        """
        suppressed: list[str] = []
        rule_fired_at = decision.timestamp

        text: str | None
        mouth_failed: bool
        if decision.source == "researcher_whisper":
            # Verbatim path: researcher's exact text goes straight to TTS.
            # No mouth call (so no §8.4 budget applies), no per-action
            # template fallback, no cached-phrase shortcut — caching is
            # keyed on persona+action templates, not arbitrary text.
            text = decision.researcher_hint
            mouth_failed = False
            if text is None or not text.strip():
                # WhisperPayload's validator enforces non-empty text, but a
                # direct caller bypassing the translator (or a malformed
                # decision row from a future migration) shouldn't crash
                # the dispatcher task — audit the abandonment instead.
                log.warning(
                    "executor.whisper_no_text",
                    decision_id=str(decision.decision_id),
                )
                return ExecutionOutcome(
                    was_executed=False,
                    llm_output=None,
                    spoken_at=None,
                    suppressed_by=["whisper_no_text"],
                    latency_ms=self._elapsed_ms(rule_fired_at),
                )
        else:
            context = extract_phrasing_context(
                state,
                target_participant_id=decision.target_participant_id,
                now=self._clock(),
            )

            # ----- Mouth (with §8.4 800ms budget) ---------------------------
            request = MouthRequest(
                action=decision.action,
                persona=self._persona,
                context=context,
            )
            text, mouth_failed = await self._invoke_mouth(request, suppressed)
            if mouth_failed:
                try:
                    text = format_template(decision.action, context)
                except NoFallbackTemplateError:
                    # No template AND mouth failed → nothing to say.
                    # Audit the abandonment without crashing the task.
                    log.warning(
                        "executor.no_phrasing_available",
                        decision_id=str(decision.decision_id),
                        action=decision.action,
                    )
                    return ExecutionOutcome(
                        was_executed=False,
                        llm_output=None,
                        spoken_at=None,
                        suppressed_by=[*suppressed, "no_phrasing_available"],
                        latency_ms=self._elapsed_ms(rule_fired_at),
                    )
                suppressed.append("llm_fallback")
        assert text is not None

        # ----- §6 latency guard before any TTS ------------------------------
        elapsed_ms = self._elapsed_ms(rule_fired_at)
        if elapsed_ms > self._total_budget_sec * 1000:
            log.warning(
                "executor.latency_exceeded_before_tts",
                decision_id=str(decision.decision_id),
                elapsed_ms=elapsed_ms,
                budget_ms=int(self._total_budget_sec * 1000),
            )
            return ExecutionOutcome(
                was_executed=False,
                llm_output=text,
                spoken_at=None,
                suppressed_by=[*suppressed, "latency_exceeded"],
                latency_ms=elapsed_ms,
            )

        # ----- Cached fallback fast path ------------------------------------
        cached = self._lookup_cached(decision.action) if mouth_failed else None
        if cached is not None:
            spoken_at = self._clock()
            await self._publisher.publish(_single_cached_chunk(cached.pcm, cached.sample_rate))
            return ExecutionOutcome(
                was_executed=True,
                llm_output=cached.text,
                spoken_at=spoken_at,
                suppressed_by=suppressed,
                latency_ms=self._elapsed_ms(rule_fired_at),
            )

        # ----- TTS streaming path -------------------------------------------
        stamp = _SpokenAtStamp(self._clock)
        try:
            frames = await self._publisher.publish(
                _stamping_iterator(
                    self._tts.synthesize(TTSRequest(voice_id=self._persona.voice_id), text),
                    stamp,
                ),
            )
        except TTSError as exc:
            log.warning(
                "executor.tts_failed",
                decision_id=str(decision.decision_id),
                error=str(exc),
            )
            return ExecutionOutcome(
                was_executed=False,
                llm_output=text,
                spoken_at=None,
                suppressed_by=[*suppressed, "tts_failed"],
                latency_ms=self._elapsed_ms(rule_fired_at),
            )

        if frames == 0 or stamp.value is None:
            # TTS yielded chunks but none reached the publisher (all
            # terminator / empty). Treat as TTS misbehaviour — audit it,
            # don't pretend we spoke.
            return ExecutionOutcome(
                was_executed=False,
                llm_output=text,
                spoken_at=None,
                suppressed_by=[*suppressed, "tts_no_audio"],
                latency_ms=self._elapsed_ms(rule_fired_at),
            )

        return ExecutionOutcome(
            was_executed=True,
            llm_output=text,
            spoken_at=stamp.value,
            suppressed_by=suppressed,
            latency_ms=self._elapsed_ms(rule_fired_at),
        )

    async def _invoke_mouth(
        self,
        request: MouthRequest,
        suppressed: list[str],
    ) -> tuple[str | None, bool]:
        """Drain the mouth into a string. Returns (text, mouth_failed)."""
        try:
            text = await asyncio.wait_for(
                _drain_mouth(self._mouth, request),
                timeout=self._mouth_budget_sec,
            )
        except TimeoutError:
            suppressed.append("mouth_timeout")
            return (None, True)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            # Don't swallow cancellation / signals — bubble out cleanly so
            # the dispatcher / shutdown path can terminate the task.
            raise
        except Exception as exc:
            log.warning("executor.mouth_failed", error=str(exc))
            suppressed.append("mouth_failed")
            return (None, True)

        if not text.strip():
            # Empty mouth output is indistinguishable from a hang for the
            # listener; force the fallback path.
            suppressed.append("mouth_empty")
            return (None, True)
        return (text, False)

    def _lookup_cached(self, action: DecisionAction) -> _CacheHit | None:
        if self._fallback_cache is None:
            return None
        entry = self._fallback_cache.get(self._persona, action)
        if entry is None:
            return None
        return _CacheHit(pcm=entry.pcm, sample_rate=entry.sample_rate, text=entry.text)

    def _elapsed_ms(self, since: datetime) -> int:
        return int((self._clock() - since).total_seconds() * 1000)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _CacheHit:
    pcm: bytes
    sample_rate: int
    text: str


class _SpokenAtStamp:
    """Records `clock()` on the first non-empty chunk it sees."""

    __slots__ = ("_clock", "value")

    def __init__(self, clock: ClockFn) -> None:
        self._clock = clock
        self.value: datetime | None = None

    def mark(self) -> None:
        if self.value is None:
            self.value = self._clock()


async def _drain_mouth(mouth: MouthClient, request: MouthRequest) -> str:
    """Concatenate every text chunk the mouth yields."""
    parts: list[str] = []
    async for chunk in mouth.phrase(request):
        if chunk.text:
            parts.append(chunk.text)
    return "".join(parts).strip()


async def _stamping_iterator(
    chunks: AsyncIterator[AudioChunk],
    stamp: _SpokenAtStamp,
) -> AsyncIterator[AudioChunk]:
    """Pass-through iterator that stamps `spoken_at` on the first non-empty chunk."""
    async for chunk in chunks:
        if not chunk.is_final and chunk.pcm:
            stamp.mark()
        yield chunk


async def _single_cached_chunk(pcm: bytes, sample_rate: int) -> AsyncIterator[AudioChunk]:
    """One-chunk async iterator emitting the cached PCM, then a terminator."""
    yield AudioChunk(pcm=pcm, sample_rate=sample_rate)
    yield AudioChunk(pcm=b"", sample_rate=sample_rate, is_final=True)
