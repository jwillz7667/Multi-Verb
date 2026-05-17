"""`DeepSeekMouth` — production mouth via DeepSeek's OpenAI-compatible API.

Brief §8.3 + §8.4 codified:
  - Default model `deepseek-chat` at https://api.deepseek.com.
  - Streams content deltas as `MouthChunk`s so TTS can begin synthesising
    before the LLM finishes — required to hit the §6 1500 ms p95
    end-of-rule-trigger to first-audible-word budget.
  - 800 ms first-token wall-clock watchdog. If the request setup OR the
    first content delta hasn't arrived in time, abort and fall back to
    the templated phrasing. Mid-stream errors after the first delta are
    swallowed (the partial sentence is forwarded with `is_final=True`)
    rather than abandoning the spoken intervention mid-word.
  - Every fallback chunk carries `from_fallback=True` so the orchestrator
    (P4 L8) can stamp `llm_fallback=true` on the decision (brief §8.4).

`max_tokens` defaults low (`60`) — moderator output is one sentence and
DeepSeek bills per output token. Tests inject a stub client; the real
`AsyncOpenAI` instance is constructed by the wiring layer with
`base_url="https://api.deepseek.com"` and the `DEEPSEEK_API_KEY` secret.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

import structlog

from verbio_engine.domain.decision import DecisionAction
from verbio_engine.mouth.prompt_builder import build_prompt
from verbio_engine.mouth.protocol import MouthChunk, MouthRequest
from verbio_engine.mouth.templates import format_template

if TYPE_CHECKING:
    from openai import AsyncOpenAI

log = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_FIRST_TOKEN_BUDGET_SEC = 0.8
DEFAULT_MAX_TOKENS = 60
DEFAULT_TEMPERATURE = 0.7


class DeepSeekMouth:
    """Streaming mouth that delegates to an injected `AsyncOpenAI` client."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str = DEFAULT_MODEL,
        first_token_budget_sec: float = DEFAULT_FIRST_TOKEN_BUDGET_SEC,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        if first_token_budget_sec <= 0:
            msg = f"first_token_budget_sec must be positive, got {first_token_budget_sec}"
            raise ValueError(msg)
        if max_tokens <= 0:
            msg = f"max_tokens must be positive, got {max_tokens}"
            raise ValueError(msg)

        self._client = client
        self._model = model
        self._first_token_budget_sec = first_token_budget_sec
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def phrase(self, request: MouthRequest) -> AsyncIterator[MouthChunk]:
        action = cast("DecisionAction", request.action)
        prompt = build_prompt(action, request.persona, request.context)
        messages = [
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": json.dumps(prompt["user"], ensure_ascii=False)},
        ]

        # Race the streaming setup + first content delta against the
        # 800 ms budget as one task; either we have a stream + first
        # delta to forward, or we fall back to the template.
        try:
            stream, first_text = await asyncio.wait_for(
                self._open_and_read_first(messages),
                timeout=self._first_token_budget_sec,
            )
        except Exception as exc:
            # TimeoutError + any underlying provider error collapse to
            # the same response: emit the §8.4 templated phrasing so
            # the intervention still lands. The orchestrator (P4 L8)
            # inspects `from_fallback` on the final chunk to set
            # `llm_fallback=true` on the persisted decision.
            log.warning(
                "deepseek_mouth_fallback",
                reason=type(exc).__name__,
                detail=str(exc) or None,
                action=request.action,
                model=self._model,
                budget_sec=self._first_token_budget_sec,
            )
            text = format_template(action, request.context)
            yield MouthChunk(text=text, is_final=True, from_fallback=True)
            return

        if first_text:
            yield MouthChunk(text=first_text)

        # Drain the rest of the stream. Mid-stream failures are logged
        # and the iterator terminates gracefully with `is_final=True`:
        # by this point TTS has likely started speaking the first delta,
        # and abandoning the utterance would be worse than truncating it.
        try:
            async for chunk in stream:
                delta = _extract_delta(chunk)
                if delta:
                    yield MouthChunk(text=delta)
        except Exception as exc:
            log.warning(
                "deepseek_mouth_stream_truncated",
                reason=type(exc).__name__,
                detail=str(exc) or None,
                action=request.action,
            )

        yield MouthChunk(text="", is_final=True)

    async def _open_and_read_first(
        self,
        messages: list[dict[str, str]],
    ) -> tuple[Any, str]:
        """Open the stream and return it alongside the first content delta.

        Returns `(stream, "")` if the stream completes before any
        non-empty delta — pathological but handled so callers don't
        need to special-case empty LLM responses.
        """
        # The SDK's `create` is overloaded on `stream`; with kwargs the
        # static signature can't narrow to AsyncStream, so we cast.
        stream = cast(
            "Any",
            await self._client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                stream=True,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            ),
        )
        async for chunk in stream:
            delta = _extract_delta(chunk)
            if delta:
                return stream, delta
        return stream, ""


def _extract_delta(chunk: Any) -> str:
    """Pull `.choices[0].delta.content` defensively.

    The OpenAI SDK guarantees the shape, but DeepSeek occasionally
    emits a chunk with no `choices` (e.g., usage-only trailing chunk
    on certain endpoints). Treat any malformed chunk as 'no content'
    rather than crashing the stream.
    """
    try:
        choices = chunk.choices
        if not choices:
            return ""
        content = choices[0].delta.content
    except (AttributeError, IndexError, TypeError):
        return ""
    return content or ""
