"""`TemplatedMouth` — fallback-only mouth, also used in shadow / smoke tests.

Tests are thin: TemplatedMouth is mostly a delegating wrapper around
`format_template`. The behaviour worth pinning is that it always marks
its single chunk as `from_fallback=True` (so the orchestrator can
stamp `llm_fallback=true` on the decision per brief §8.4) and that it
respects the per-action / targeted-vs-untargeted template logic.
"""

from __future__ import annotations

import pytest

from verbio_engine.mouth.context import PhrasingContext
from verbio_engine.mouth.persona import ModeratorPersona
from verbio_engine.mouth.protocol import MouthChunk, MouthClient, MouthRequest
from verbio_engine.mouth.templated import TemplatedMouth
from verbio_engine.mouth.templates import NoFallbackTemplateError

PERSONA = ModeratorPersona(style_prompt="Calm moderator.", voice_id="v1")


def _request(action: str, context: PhrasingContext | None = None) -> MouthRequest:
    return MouthRequest(
        action=action,
        persona=PERSONA,
        context=context if context is not None else PhrasingContext(),
    )


async def _collect(mouth: TemplatedMouth, request: MouthRequest) -> list[MouthChunk]:
    chunks: list[MouthChunk] = []
    async for chunk in mouth.phrase(request):
        chunks.append(chunk)
    return chunks


def test_satisfies_mouth_client_protocol() -> None:
    assert isinstance(TemplatedMouth(), MouthClient)


async def test_emits_single_chunk_marked_final_and_fallback() -> None:
    mouth = TemplatedMouth()
    chunks = await _collect(mouth, _request("redirect_topic"))
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.is_final is True
    # Every TemplatedMouth chunk carries the fallback marker — the
    # orchestrator uses it to set `llm_fallback=true` on the decision.
    assert chunk.from_fallback is True
    assert chunk.text == "Thanks — let's bring it back to the original question."


async def test_uses_targeted_variant_when_name_present() -> None:
    mouth = TemplatedMouth()
    chunks = await _collect(
        mouth,
        _request("prompt_participant", PhrasingContext(target_display_name="Alice")),
    )
    assert chunks[0].text == "Alice, I'd love to hear your thoughts on this."


async def test_uses_untargeted_variant_when_no_name() -> None:
    mouth = TemplatedMouth()
    chunks = await _collect(mouth, _request("prompt_participant"))
    assert chunks[0].text == "I'd love to hear another perspective on this."


async def test_raises_for_stay_silent() -> None:
    # Loud failure if the orchestrator ever routes stay_silent here —
    # there's no fallback for "don't speak" by design.
    mouth = TemplatedMouth()
    with pytest.raises(NoFallbackTemplateError, match="stay_silent"):
        await _collect(mouth, _request("stay_silent"))


async def test_raises_for_close_session() -> None:
    # close_session is a lifecycle event handled in Phase 5, not a
    # spoken intervention.
    mouth = TemplatedMouth()
    with pytest.raises(NoFallbackTemplateError, match="close_session"):
        await _collect(mouth, _request("close_session"))
