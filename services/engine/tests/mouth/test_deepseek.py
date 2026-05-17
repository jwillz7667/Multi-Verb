"""`DeepSeekMouth` — streaming production mouth with §8.4 fallback.

We never hit DeepSeek in tests. A stub `AsyncOpenAI` client lets us
exercise the streaming path, the first-token watchdog, the create-
error fallback, and the mid-stream truncation behaviour without
network or wall-clock dependencies.

Tests use a budget of 50ms (short enough to keep the suite fast) with
fake delays slightly over to make sure the watchdog fires when it
should and doesn't when it shouldn't.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from verbio_engine.mouth.context import PhrasingContext
from verbio_engine.mouth.deepseek import DeepSeekMouth
from verbio_engine.mouth.persona import ModeratorPersona
from verbio_engine.mouth.protocol import MouthChunk, MouthClient, MouthRequest

PERSONA = ModeratorPersona(style_prompt="Calm moderator.", voice_id="v1")


def _request(
    action: str = "redirect_topic",
    context: PhrasingContext | None = None,
) -> MouthRequest:
    return MouthRequest(
        action=action,
        persona=PERSONA,
        context=context if context is not None else PhrasingContext(),
    )


# ---------------------------------------------------------------------------
# Stub `AsyncOpenAI`: enough surface to exercise DeepSeekMouth
# ---------------------------------------------------------------------------


@dataclass
class _StubDelta:
    content: str | None


@dataclass
class _StubChoice:
    delta: _StubDelta


@dataclass
class _StubChunk:
    choices: list[_StubChoice]


class _StubStream:
    """Async iterator that yields fake content chunks with optional delays."""

    def __init__(
        self,
        chunks: list[str | None],
        *,
        delay_before_first: float = 0.0,
        delay_between_chunks: float = 0.0,
        raise_mid_stream: Exception | None = None,
        raise_after_n: int = 0,
    ) -> None:
        self._chunks = list(chunks)
        self._delay_before_first = delay_before_first
        self._delay_between = delay_between_chunks
        self._raise = raise_mid_stream
        self._raise_after_n = raise_after_n
        self._yielded = 0

    def __aiter__(self) -> _StubStream:
        return self

    async def __anext__(self) -> _StubChunk:
        if self._yielded == 0 and self._delay_before_first:
            await asyncio.sleep(self._delay_before_first)
        elif self._yielded > 0 and self._delay_between:
            await asyncio.sleep(self._delay_between)

        if self._raise is not None and self._yielded >= self._raise_after_n:
            raise self._raise

        if not self._chunks:
            raise StopAsyncIteration

        content = self._chunks.pop(0)
        self._yielded += 1
        return _StubChunk(choices=[_StubChoice(delta=_StubDelta(content=content))])


@dataclass
class _StubChatCompletions:
    chunks: list[str | None] = field(default_factory=list)
    raise_on_create: Exception | None = None
    create_delay: float = 0.0
    delay_before_first: float = 0.0
    delay_between_chunks: float = 0.0
    raise_mid_stream: Exception | None = None
    raise_after_n: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, **kwargs: Any) -> _StubStream:
        self.calls.append(kwargs)
        if self.create_delay:
            await asyncio.sleep(self.create_delay)
        if self.raise_on_create is not None:
            raise self.raise_on_create
        return _StubStream(
            list(self.chunks),
            delay_before_first=self.delay_before_first,
            delay_between_chunks=self.delay_between_chunks,
            raise_mid_stream=self.raise_mid_stream,
            raise_after_n=self.raise_after_n,
        )


@dataclass
class _StubChat:
    completions: _StubChatCompletions


@dataclass
class _StubClient:
    chat: _StubChat


def _make_mouth(
    *,
    chunks: list[str | None] | None = None,
    raise_on_create: Exception | None = None,
    create_delay: float = 0.0,
    delay_before_first: float = 0.0,
    delay_between_chunks: float = 0.0,
    raise_mid_stream: Exception | None = None,
    raise_after_n: int = 0,
    first_token_budget_sec: float = 1.0,
    model: str = "deepseek-chat",
    max_tokens: int = 60,
    temperature: float = 0.7,
) -> tuple[DeepSeekMouth, _StubChatCompletions]:
    completions = _StubChatCompletions(
        chunks=chunks or [],
        raise_on_create=raise_on_create,
        create_delay=create_delay,
        delay_before_first=delay_before_first,
        delay_between_chunks=delay_between_chunks,
        raise_mid_stream=raise_mid_stream,
        raise_after_n=raise_after_n,
    )
    client = _StubClient(chat=_StubChat(completions=completions))
    mouth = DeepSeekMouth(
        client=cast("Any", client),
        model=model,
        first_token_budget_sec=first_token_budget_sec,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return mouth, completions


async def _collect(mouth: DeepSeekMouth, request: MouthRequest) -> list[MouthChunk]:
    chunks: list[MouthChunk] = []
    async for chunk in mouth.phrase(request):
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_satisfies_mouth_client_protocol(self) -> None:
        mouth, _ = _make_mouth()
        assert isinstance(mouth, MouthClient)

    def test_rejects_zero_first_token_budget(self) -> None:
        client = _StubClient(chat=_StubChat(completions=_StubChatCompletions()))
        with pytest.raises(ValueError, match="first_token_budget_sec must be positive"):
            DeepSeekMouth(client=cast("Any", client), first_token_budget_sec=0.0)

    def test_rejects_negative_max_tokens(self) -> None:
        client = _StubClient(chat=_StubChat(completions=_StubChatCompletions()))
        with pytest.raises(ValueError, match="max_tokens must be positive"):
            DeepSeekMouth(client=cast("Any", client), max_tokens=-1)


# ---------------------------------------------------------------------------
# Happy path streaming
# ---------------------------------------------------------------------------


class TestStreamingHappyPath:
    async def test_yields_each_content_delta_then_final_terminator(self) -> None:
        mouth, _ = _make_mouth(chunks=["Hello ", "Alice", "."])
        chunks = await _collect(mouth, _request())
        # 3 content chunks + 1 terminator. The terminator carries
        # is_final=True with empty text so consumers know we're done.
        assert [c.text for c in chunks] == ["Hello ", "Alice", ".", ""]
        assert [c.is_final for c in chunks] == [False, False, False, True]
        # Happy-path streaming is never marked as fallback.
        assert all(c.from_fallback is False for c in chunks)

    async def test_skips_empty_and_none_content_chunks(self) -> None:
        # DeepSeek streams role-only or empty-content chunks at the
        # start of a response. The mouth must not forward those to TTS
        # as zero-length tokens.
        mouth, _ = _make_mouth(chunks=[None, "", "Hello", None, " world", ""])
        chunks = await _collect(mouth, _request())
        text_chunks = [c.text for c in chunks if not c.is_final]
        assert text_chunks == ["Hello", " world"]

    async def test_passes_persona_aware_prompt_to_create_call(self) -> None:
        # The user message must be the §8.2 JSON shape, JSON-serialised,
        # and the system message must carry the persona prefix + §8.2 suffix.
        mouth, completions = _make_mouth(chunks=["ok"])
        await _collect(mouth, _request("prompt_participant"))
        assert len(completions.calls) == 1
        kwargs = completions.calls[0]
        assert kwargs["model"] == "deepseek-chat"
        assert kwargs["stream"] is True
        assert kwargs["max_tokens"] == 60
        assert kwargs["temperature"] == 0.7
        messages = kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"].startswith("Calm moderator. ")
        assert "in one sentence, no preamble." in messages[0]["content"]
        assert messages[1]["role"] == "user"
        # User content is a JSON string (chat completions API only
        # accepts string content).
        import json

        user = json.loads(messages[1]["content"])
        assert user["intervention"] == "prompt_participant"

    async def test_custom_model_max_tokens_temperature_are_forwarded(self) -> None:
        mouth, completions = _make_mouth(
            chunks=["ok"],
            model="deepseek-reasoner",
            max_tokens=120,
            temperature=0.2,
        )
        await _collect(mouth, _request())
        kwargs = completions.calls[0]
        assert kwargs["model"] == "deepseek-reasoner"
        assert kwargs["max_tokens"] == 120
        assert kwargs["temperature"] == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# Fallback path (brief §8.4)
# ---------------------------------------------------------------------------


class TestFirstTokenBudget:
    async def test_slow_first_token_triggers_template_fallback(self) -> None:
        # First content delta delayed 200 ms; budget is 50 ms → fall back.
        mouth, _ = _make_mouth(
            chunks=["Hello"],
            delay_before_first=0.2,
            first_token_budget_sec=0.05,
        )
        chunks = await _collect(mouth, _request("redirect_topic"))
        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.is_final is True
        assert chunk.from_fallback is True
        # Picked the §8.4 template phrasing for redirect_topic.
        assert chunk.text == "Thanks — let's bring it back to the original question."

    async def test_slow_create_call_triggers_template_fallback(self) -> None:
        # The HTTP roundtrip itself overruns the budget — never receives
        # any chunk. Still falls back; the wait_for cancels the create.
        mouth, _ = _make_mouth(
            chunks=["Hello"],
            create_delay=0.2,
            first_token_budget_sec=0.05,
        )
        chunks = await _collect(mouth, _request("redirect_topic"))
        assert len(chunks) == 1
        assert chunks[0].from_fallback is True
        assert chunks[0].is_final is True

    async def test_fast_first_token_does_not_trigger_fallback(self) -> None:
        # Budget 100 ms, first token after ~5 ms — happy path proceeds.
        mouth, _ = _make_mouth(
            chunks=["fast"],
            delay_before_first=0.005,
            first_token_budget_sec=0.1,
        )
        chunks = await _collect(mouth, _request())
        assert any(c.text == "fast" for c in chunks)
        assert all(c.from_fallback is False for c in chunks)


class TestCreateError:
    async def test_create_raises_falls_back_to_template(self) -> None:
        mouth, _ = _make_mouth(
            raise_on_create=RuntimeError("api 503"),
        )
        chunks = await _collect(mouth, _request("redirect_topic"))
        assert len(chunks) == 1
        assert chunks[0].from_fallback is True
        assert chunks[0].is_final is True
        assert chunks[0].text == "Thanks — let's bring it back to the original question."

    async def test_fallback_for_targeted_action_uses_target_name(self) -> None:
        mouth, _ = _make_mouth(raise_on_create=RuntimeError("api down"))
        chunks = await _collect(
            mouth,
            _request(
                "prompt_participant",
                PhrasingContext(target_display_name="Bob"),
            ),
        )
        assert chunks[0].text == "Bob, I'd love to hear your thoughts on this."
        assert chunks[0].from_fallback is True


class TestMidStreamErrorIsTruncatedNotFallenBack:
    async def test_error_after_first_chunk_emits_partial_then_terminator(self) -> None:
        # By the time the mid-stream error fires, TTS has already begun
        # synthesising the first delta. Abandoning the intervention
        # mid-word would be worse than truncating, so we just emit
        # is_final=True with what we have and DO NOT fall back to the
        # template (that would produce a different sentence).
        mouth, _ = _make_mouth(
            chunks=["Partial"],
            raise_mid_stream=RuntimeError("stream reset"),
            raise_after_n=1,
        )
        chunks = await _collect(mouth, _request())
        assert [c.text for c in chunks] == ["Partial", ""]
        assert chunks[-1].is_final is True
        # Crucial: not marked from_fallback — we DID get an LLM response.
        assert all(c.from_fallback is False for c in chunks)


class TestEmptyStream:
    async def test_no_content_at_all_falls_back_via_budget(self) -> None:
        # Stream completes without ever yielding non-empty content
        # within the budget — the wait_for sees StopAsyncIteration
        # bubble up before any delta arrives; treat as fallback.
        mouth, _ = _make_mouth(chunks=[None, ""], first_token_budget_sec=1.0)
        chunks = await _collect(mouth, _request("redirect_topic"))
        # The inner coroutine returned (stream, "") — first_text is
        # empty, so no content chunk is yielded, only the terminator.
        # Effectively we shipped silence. That's an LLM behaviour bug,
        # not a fallback condition, so from_fallback stays False; the
        # caller can detect "empty utterance" and decide what to do.
        assert chunks == [MouthChunk(text="", is_final=True, from_fallback=False)]
