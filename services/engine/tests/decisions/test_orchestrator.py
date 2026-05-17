"""`DecisionExecutor` — mouth/TTS/publisher coordination + budget enforcement.

Coverage targets are the §6 + §8.4 + §9 contracts the executor encodes:

  * Happy path: mouth yields text, TTS yields chunks, publisher captures —
    outcome is `was_executed=True` with `spoken_at` stamped at first frame.
  * §8.4 mouth budget: a slow mouth gets cancelled at 800 ms and the
    templated phrasing takes over; outcome carries `suppressed_by` codes
    so the audit log shows which path ran.
  * §9 cached fallback: when the mouth fails AND the persona has a cache
    entry, TTS is skipped and cached PCM is published instantly.
  * §6 latency guard: rule_fired_at → now > 1500 ms before TTS aborts
    the run with `suppressed_by=["latency_exceeded"]` and
    `was_executed=False` — the audit trail invariant.
  * TTS failures: provider error surfaces as `tts_failed`; an
    empty-stream TTS surfaces as `tts_no_audio`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from verbio_engine.decisions.orchestrator import (
    DEFAULT_MOUTH_BUDGET_MS,
    DEFAULT_TOTAL_BUDGET_MS,
    DecisionExecutor,
)
from verbio_engine.domain.decision import DecisionAction, ModeratorDecision
from verbio_engine.domain.session_state import SessionState
from verbio_engine.mouth import (
    FallbackPhraseCache,
    ModeratorPersona,
    MouthChunk,
    MouthRequest,
)
from verbio_engine.tts.protocol import AudioChunk, TTSError, TTSRequest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DECISION_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
RULE_FIRED_AT = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeMouth:
    """Configurable `MouthClient` stub."""

    text: str = "Could you say more about that?"
    sleep_sec: float = 0.0
    raise_exc: Exception | None = None
    calls: list[MouthRequest] = field(default_factory=list)

    def phrase(self, request: MouthRequest) -> AsyncIterator[MouthChunk]:
        return self._stream(request)

    async def _stream(self, request: MouthRequest) -> AsyncIterator[MouthChunk]:
        self.calls.append(request)
        if self.sleep_sec > 0:
            await asyncio.sleep(self.sleep_sec)
        if self.raise_exc is not None:
            raise self.raise_exc
        # Split in two chunks + final terminator.
        mid = max(1, len(self.text) // 2)
        yield MouthChunk(text=self.text[:mid])
        yield MouthChunk(text=self.text[mid:])
        yield MouthChunk(text="", is_final=True)


@dataclass
class _FakeTTS:
    """Configurable `TTSClient` stub. Yields PCM derived from input text."""

    raise_exc: Exception | None = None
    empty_stream: bool = False
    sample_rate: int = 24000
    calls: list[tuple[TTSRequest, str]] = field(default_factory=list)

    def synthesize(self, request: TTSRequest, text: str) -> AsyncIterator[AudioChunk]:
        return self._stream(request, text)

    async def _stream(self, request: TTSRequest, text: str) -> AsyncIterator[AudioChunk]:
        self.calls.append((request, text))
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.empty_stream:
            yield AudioChunk(pcm=b"", sample_rate=self.sample_rate, is_final=True)
            return
        body = text.encode("utf-8")
        # Two non-empty chunks then a terminator.
        mid = max(2, len(body) // 2)
        yield AudioChunk(pcm=body[:mid], sample_rate=self.sample_rate)
        yield AudioChunk(pcm=body[mid:], sample_rate=self.sample_rate)
        yield AudioChunk(pcm=b"", sample_rate=self.sample_rate, is_final=True)


@dataclass
class _FakePublisher:
    """Stand-in for `ModeratorAudioPublisher.publish` that counts frames."""

    captured: list[AudioChunk] = field(default_factory=list)

    async def publish(self, chunks: AsyncIterator[AudioChunk]) -> int:
        n = 0
        async for chunk in chunks:
            self.captured.append(chunk)
            if not chunk.is_final and chunk.pcm:
                n += 1
        return n


class _ManualClock:
    """Deterministic clock that advances only when `.tick()` is called."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def tick(self, ms: int) -> None:
        self._now = self._now + timedelta(milliseconds=ms)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _persona(*, voice_id: str = "voice-1") -> ModeratorPersona:
    return ModeratorPersona(
        style_prompt="Test persona",
        tone="warm",
        formality="neutral",
        voice_provider="cartesia",
        voice_id=voice_id,
    )


def _decision(
    *,
    action: DecisionAction = "prompt_participant",
    target: str | None = "alice",
    when: datetime | None = None,
) -> ModeratorDecision:
    return ModeratorDecision(
        decision_id=DECISION_ID,
        session_id=SESSION_ID,
        tick_id=0,
        timestamp=when if when is not None else RULE_FIRED_AT,
        action=action,
        target_participant_id=target,
        source="auto",
        triggering_rule="silence_gap",
        cooldown_until=(when or RULE_FIRED_AT) + timedelta(seconds=45),
    )


def _state() -> SessionState:
    return SessionState(
        session_id=SESSION_ID,
        tick_id=0,
        t=RULE_FIRED_AT,
        started_at=RULE_FIRED_AT - timedelta(minutes=2),
        elapsed_sec=120.0,
        participants={},
    )


def _executor(
    *,
    mouth: _FakeMouth | None = None,
    tts: _FakeTTS | None = None,
    publisher: _FakePublisher | None = None,
    fallback_cache: FallbackPhraseCache | None = None,
    clock: _ManualClock | None = None,
    mouth_budget_ms: int = DEFAULT_MOUTH_BUDGET_MS,
    total_budget_ms: int = DEFAULT_TOTAL_BUDGET_MS,
    persona: ModeratorPersona | None = None,
) -> tuple[DecisionExecutor, _FakeMouth, _FakeTTS, _FakePublisher, _ManualClock]:
    m = mouth if mouth is not None else _FakeMouth()
    t = tts if tts is not None else _FakeTTS()
    p = publisher if publisher is not None else _FakePublisher()
    c = clock if clock is not None else _ManualClock(RULE_FIRED_AT)
    pp = persona if persona is not None else _persona()
    executor = DecisionExecutor(
        mouth=m,
        tts=t,
        publisher=p,
        persona=pp,
        clock=c,
        fallback_cache=fallback_cache,
        mouth_budget_ms=mouth_budget_ms,
        total_budget_ms=total_budget_ms,
    )
    return executor, m, t, p, c


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_rejects_non_positive_mouth_budget(self) -> None:
        with pytest.raises(ValueError, match="mouth_budget_ms must be positive"):
            _executor(mouth_budget_ms=0)
        with pytest.raises(ValueError, match="mouth_budget_ms must be positive"):
            _executor(mouth_budget_ms=-1)

    def test_rejects_non_positive_total_budget(self) -> None:
        with pytest.raises(ValueError, match="total_budget_ms must be positive"):
            _executor(total_budget_ms=0)
        with pytest.raises(ValueError, match="total_budget_ms must be positive"):
            _executor(total_budget_ms=-1)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    async def test_mouth_to_tts_to_publisher_emits_executed_outcome(self) -> None:
        executor, mouth, tts, publisher, _clock = _executor()

        outcome = await executor.execute(_decision(), _state())

        assert outcome.was_executed is True
        assert outcome.llm_output == mouth.text
        assert outcome.spoken_at is not None
        assert outcome.suppressed_by == []
        # TTS was called with the mouth's full text.
        assert tts.calls == [(TTSRequest(voice_id="voice-1"), mouth.text)]
        # Publisher captured non-empty PCM chunks + terminator.
        non_terminator = [c for c in publisher.captured if not c.is_final]
        assert len(non_terminator) == 2
        assert b"".join(c.pcm for c in non_terminator) == mouth.text.encode("utf-8")

    async def test_mouth_request_carries_persona_and_phrasing_context(self) -> None:
        executor, mouth, _tts, _pub, _clock = _executor()

        await executor.execute(_decision(action="redirect_topic", target=None), _state())

        request = mouth.calls[0]
        assert request.action == "redirect_topic"
        assert request.persona.voice_id == "voice-1"
        # Untargeted action → no target_display_name in context.
        assert request.context.target_display_name is None

    async def test_spoken_at_is_stamped_at_first_non_empty_chunk(self) -> None:
        clock = _ManualClock(RULE_FIRED_AT)
        executor, _mouth, _tts, _pub, _ = _executor(clock=clock)

        outcome = await executor.execute(_decision(), _state())

        # Clock didn't tick; spoken_at should equal the moment of capture
        # (which under the manual clock is identically RULE_FIRED_AT).
        assert outcome.spoken_at == RULE_FIRED_AT


# ---------------------------------------------------------------------------
# §8.4 mouth budget
# ---------------------------------------------------------------------------


class TestMouthBudget:
    async def test_slow_mouth_times_out_and_template_takes_over(self) -> None:
        # Mouth sleeps longer than the 50 ms test budget; template fills in.
        mouth = _FakeMouth(sleep_sec=0.5, text="real LLM phrasing")
        executor, _m, tts, _pub, _clock = _executor(mouth=mouth, mouth_budget_ms=50)

        outcome = await executor.execute(_decision(action="redirect_topic"), _state())

        assert outcome.was_executed is True
        assert "mouth_timeout" in outcome.suppressed_by
        assert "llm_fallback" in outcome.suppressed_by
        # The template text was sent to TTS, not the LLM phrasing.
        assert tts.calls[0][1] != "real LLM phrasing"
        assert "bring it back" in tts.calls[0][1].lower()

    async def test_mouth_error_falls_through_to_template(self) -> None:
        mouth = _FakeMouth(raise_exc=RuntimeError("upstream provider down"))
        executor, _m, tts, _pub, _clock = _executor(mouth=mouth)

        outcome = await executor.execute(_decision(action="redirect_topic"), _state())

        assert outcome.was_executed is True
        assert "mouth_failed" in outcome.suppressed_by
        assert "llm_fallback" in outcome.suppressed_by
        # Template was synth'd.
        assert tts.calls[0][1] != "upstream provider down"
        assert "bring it back" in tts.calls[0][1].lower()

    async def test_empty_mouth_output_also_triggers_fallback(self) -> None:
        # Mouth returns no text — indistinguishable from a hang for the
        # listener; force the fallback path.
        mouth = _FakeMouth(text="")
        executor, _m, tts, _pub, _clock = _executor(mouth=mouth)

        outcome = await executor.execute(_decision(action="redirect_topic"), _state())

        assert outcome.was_executed is True
        assert "mouth_empty" in outcome.suppressed_by
        assert "llm_fallback" in outcome.suppressed_by
        assert tts.calls[0][1]  # something was synth'd


# ---------------------------------------------------------------------------
# §9 cached fallback fast path
# ---------------------------------------------------------------------------


class _CacheWarmerTTS:
    """TTS stub used to warm the FallbackPhraseCache with deterministic PCM."""

    def synthesize(self, request: TTSRequest, text: str) -> AsyncIterator[AudioChunk]:
        return self._stream(request, text)

    async def _stream(self, request: TTSRequest, text: str) -> AsyncIterator[AudioChunk]:
        yield AudioChunk(pcm=text.encode("utf-8"), sample_rate=request.sample_rate)
        yield AudioChunk(pcm=b"", sample_rate=request.sample_rate, is_final=True)


class TestCachedFallback:
    async def test_uses_cache_when_mouth_fails_and_skips_tts(self) -> None:
        persona = _persona()
        cache = FallbackPhraseCache()
        await cache.warm(persona, _CacheWarmerTTS())

        mouth = _FakeMouth(raise_exc=RuntimeError("LLM dead"))
        executor, _m, tts, publisher, _clock = _executor(
            persona=persona,
            mouth=mouth,
            fallback_cache=cache,
        )

        outcome = await executor.execute(_decision(action="redirect_topic"), _state())

        assert outcome.was_executed is True
        # llm_output reflects the cached text — useful for the audit row.
        assert outcome.llm_output is not None
        assert "bring it back" in outcome.llm_output.lower()
        assert "mouth_failed" in outcome.suppressed_by
        # Crucially: TTS was NOT called.
        assert tts.calls == []
        # The cached PCM was published.
        non_terminator = [c for c in publisher.captured if not c.is_final]
        assert len(non_terminator) == 1

    async def test_cache_unused_on_mouth_success(self) -> None:
        # Mouth succeeded → no fallback needed → cache is irrelevant.
        persona = _persona()
        cache = FallbackPhraseCache()
        await cache.warm(persona, _CacheWarmerTTS())

        executor, _m, tts, _pub, _clock = _executor(
            persona=persona,
            fallback_cache=cache,
        )

        outcome = await executor.execute(_decision(), _state())

        assert outcome.was_executed is True
        # TTS WAS called (cache only fires on fallback path).
        assert len(tts.calls) == 1

    async def test_cache_miss_falls_through_to_tts(self) -> None:
        # Cache warmed under a different persona; current persona has no entries.
        cache = FallbackPhraseCache()
        await cache.warm(_persona(voice_id="other-voice"), _CacheWarmerTTS())

        mouth = _FakeMouth(raise_exc=RuntimeError("LLM dead"))
        executor, _m, tts, _pub, _clock = _executor(
            mouth=mouth,
            fallback_cache=cache,
        )

        outcome = await executor.execute(_decision(action="redirect_topic"), _state())

        assert outcome.was_executed is True
        # Template went to TTS, not cached PCM.
        assert len(tts.calls) == 1


# ---------------------------------------------------------------------------
# §6 latency guard
# ---------------------------------------------------------------------------


class TestLatencyGuard:
    async def test_aborts_when_elapsed_exceeds_total_budget(self) -> None:
        # Manual clock that jumps past the 1500 ms total budget the moment
        # the mouth finishes. We use a slow real mouth (50 ms sleep) and a
        # tight total budget so the elapsed_ms check fires.
        clock = _BurningClock(start=RULE_FIRED_AT, jump_ms=2000)
        executor, _m, tts, publisher, _clock = _executor(
            clock=clock,
            mouth_budget_ms=DEFAULT_MOUTH_BUDGET_MS,
            total_budget_ms=DEFAULT_TOTAL_BUDGET_MS,
        )

        outcome = await executor.execute(_decision(), _state())

        assert outcome.was_executed is False
        assert outcome.spoken_at is None
        assert outcome.suppressed_by == ["latency_exceeded"]
        # llm_output is preserved — the mouth ran successfully even
        # though we abandoned execution.
        assert outcome.llm_output is not None
        # Critical: TTS / publisher were never reached.
        assert tts.calls == []
        assert publisher.captured == []
        # latency_ms reports the actual elapsed wall clock.
        assert outcome.latency_ms >= DEFAULT_TOTAL_BUDGET_MS


class _BurningClock:
    """Clock that returns `start` once, then `start + jump_ms` forever after.

    Used by the latency-guard test. The executor calls `clock()` once
    when extracting phrasing context (before mouth), then again after
    mouth finishes to compute elapsed_ms for the §6 guard. We want the
    second call (and every later call) to land past the total budget so
    the guard fires.
    """

    def __init__(self, *, start: datetime, jump_ms: int) -> None:
        self._start = start
        self._after = start + timedelta(milliseconds=jump_ms)
        self._reads = 0

    def __call__(self) -> datetime:
        self._reads += 1
        return self._start if self._reads == 1 else self._after


# ---------------------------------------------------------------------------
# TTS failures
# ---------------------------------------------------------------------------


class TestTTSFailures:
    async def test_tts_error_records_tts_failed(self) -> None:
        tts = _FakeTTS(raise_exc=TTSError("provider 500"))
        executor, _m, _tts, publisher, _clock = _executor(tts=tts)

        outcome = await executor.execute(_decision(), _state())

        assert outcome.was_executed is False
        assert outcome.spoken_at is None
        assert outcome.suppressed_by == ["tts_failed"]
        # llm_output stays populated so researchers can see what *would*
        # have been spoken even though no audio was emitted.
        assert outcome.llm_output is not None
        assert publisher.captured == []

    async def test_empty_tts_stream_records_tts_no_audio(self) -> None:
        # TTS emits only the terminator chunk — publisher pushes 0 frames.
        tts = _FakeTTS(empty_stream=True)
        executor, _m, _tts, publisher, _clock = _executor(tts=tts)

        outcome = await executor.execute(_decision(), _state())

        assert outcome.was_executed is False
        assert "tts_no_audio" in outcome.suppressed_by
        # Terminator was passed through.
        assert any(c.is_final for c in publisher.captured)


# ---------------------------------------------------------------------------
# Untargeted phrasings (no template would need target_display_name)
# ---------------------------------------------------------------------------


class TestUntargetedActions:
    @pytest.mark.parametrize(
        "action",
        ["redirect_topic", "summarize_thread", "suggest_turn_taking"],
    )
    async def test_untargeted_action_executes_without_a_target(
        self,
        action: DecisionAction,
    ) -> None:
        executor, mouth, tts, _pub, _clock = _executor()

        outcome = await executor.execute(_decision(action=action, target=None), _state())

        assert outcome.was_executed is True
        assert mouth.calls[0].context.target_display_name is None
        assert tts.calls


# ---------------------------------------------------------------------------
# Whisper path (P5 L4)
# ---------------------------------------------------------------------------


def _whisper_decision(
    *,
    text: str | None = "Maria, your thoughts on the price?",
    target: str | None = "alice",
    when: datetime | None = None,
) -> ModeratorDecision:
    """Researcher-whisper decision — source set so the executor skips the mouth."""
    return ModeratorDecision(
        decision_id=DECISION_ID,
        session_id=SESSION_ID,
        tick_id=0,
        timestamp=when if when is not None else RULE_FIRED_AT,
        action="prompt_participant",
        target_participant_id=target,
        source="researcher_whisper",
        triggering_rule=None,
        researcher_id=str(uuid.uuid4()),
        researcher_hint=text,
        reason_codes=["researcher_command:whisper"],
        confidence=1.0,
        cooldown_until=(when or RULE_FIRED_AT) + timedelta(seconds=3),
    )


class TestWhisperPath:
    """Whisper decisions bypass the mouth: verbatim `researcher_hint` → TTS.

    The mouth must not be invoked (no §8.4 budget, no template fallback,
    no cache lookup); the executor pipes the researcher's exact words
    straight into TTS and onto the publisher. The §6 latency guard still
    runs so a backlogged tick doesn't speak stale words.
    """

    async def test_whisper_skips_mouth_and_sends_verbatim_text_to_tts(self) -> None:
        executor, mouth, tts, publisher, _clock = _executor()

        outcome = await executor.execute(
            _whisper_decision(text="Maria, your thoughts on the price?"),
            _state(),
        )

        assert outcome.was_executed is True
        # The mouth is the load-bearing assertion: it must not be invoked.
        assert mouth.calls == []
        # TTS received the EXACT researcher text — not the mouth's
        # default phrasing, not a template, not a cache entry.
        assert len(tts.calls) == 1
        sent_request, sent_text = tts.calls[0]
        assert sent_request == TTSRequest(voice_id="voice-1")
        assert sent_text == "Maria, your thoughts on the price?"
        # llm_output reflects what was spoken (the verbatim text), so
        # the audit row can render "moderator said: …" without ambiguity.
        assert outcome.llm_output == "Maria, your thoughts on the price?"
        assert outcome.spoken_at is not None
        # No fallback codes: the whisper path doesn't run mouth/template logic.
        assert outcome.suppressed_by == []
        # PCM ends up on the publisher.
        non_terminator = [c for c in publisher.captured if not c.is_final]
        assert len(non_terminator) == 2
        assert b"".join(c.pcm for c in non_terminator) == b"Maria, your thoughts on the price?"

    async def test_whisper_ignores_fallback_cache_even_when_warm(self) -> None:
        # Sanity check the bypass: a warm cache must not steal the
        # verbatim text. The cache is keyed by persona+action templates,
        # which are categorically wrong for researcher-typed words.
        persona = _persona()
        cache = FallbackPhraseCache()
        await cache.warm(persona, _CacheWarmerTTS())

        executor, mouth, tts, _pub, _clock = _executor(
            persona=persona,
            fallback_cache=cache,
        )

        outcome = await executor.execute(
            _whisper_decision(text="researcher's literal words"),
            _state(),
        )

        assert outcome.was_executed is True
        assert mouth.calls == []
        # TTS still ran with the verbatim text — cache stayed unused.
        assert len(tts.calls) == 1
        assert tts.calls[0][1] == "researcher's literal words"

    async def test_whisper_with_none_hint_abandons_with_audit_code(self) -> None:
        # The translator's WhisperPayload enforces non-empty text, but a
        # direct caller could in principle build a decision with no hint.
        # The executor must audit-and-abandon rather than crash.
        executor, mouth, tts, publisher, _clock = _executor()

        outcome = await executor.execute(_whisper_decision(text=None), _state())

        assert outcome.was_executed is False
        assert outcome.spoken_at is None
        assert outcome.llm_output is None
        assert outcome.suppressed_by == ["whisper_no_text"]
        # Neither mouth, TTS, nor publisher were touched.
        assert mouth.calls == []
        assert tts.calls == []
        assert publisher.captured == []

    async def test_whisper_with_whitespace_only_hint_abandons(self) -> None:
        # Same guard for whitespace — a "   " text would publish silence.
        executor, _m, tts, publisher, _clock = _executor()

        outcome = await executor.execute(_whisper_decision(text="   "), _state())

        assert outcome.was_executed is False
        assert outcome.suppressed_by == ["whisper_no_text"]
        assert tts.calls == []
        assert publisher.captured == []

    async def test_whisper_tts_failure_records_tts_failed(self) -> None:
        # The whisper path still benefits from the executor's TTS error
        # handling — outcome flags `tts_failed`, audit row stays truthful.
        tts = _FakeTTS(raise_exc=TTSError("provider 500"))
        executor, _m, _tts, publisher, _clock = _executor(tts=tts)

        outcome = await executor.execute(_whisper_decision(), _state())

        assert outcome.was_executed is False
        assert outcome.suppressed_by == ["tts_failed"]
        # llm_output stays populated with the would-have-been-spoken
        # text so researchers can see what the moderator was about to say.
        assert outcome.llm_output == "Maria, your thoughts on the price?"
        assert publisher.captured == []

    async def test_whisper_respects_total_latency_guard(self) -> None:
        # The §6 guard is universal: if the tick is already past the
        # 1500ms budget by the time we reach TTS, we abandon and audit
        # `latency_exceeded`. Whisper has no mouth call to burn the
        # budget, so the clock starts already-past-budget (e.g., the
        # tick spent its budget on a slow command-drain → persist before
        # ever calling the executor).
        clock = _ManualClock(RULE_FIRED_AT + timedelta(milliseconds=2000))
        executor, _m, tts, publisher, _clock = _executor(clock=clock)

        outcome = await executor.execute(_whisper_decision(), _state())

        assert outcome.was_executed is False
        assert outcome.suppressed_by == ["latency_exceeded"]
        # llm_output keeps the researcher's text for the audit row.
        assert outcome.llm_output == "Maria, your thoughts on the price?"
        # TTS never ran.
        assert tts.calls == []
        assert publisher.captured == []
        assert outcome.latency_ms >= DEFAULT_TOTAL_BUDGET_MS
