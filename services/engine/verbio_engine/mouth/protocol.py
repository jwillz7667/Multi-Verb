"""`MouthClient` — the contract every mouth implementation honours (brief §8).

V1 implementations:
  - `DeepSeekMouth` (P4 L2) — streams from api.deepseek.com.
  - `TemplatedMouth` (P4 L2) — returns the §8.4 fallback as one chunk.

Both yield `MouthChunk` records so a downstream TTS client can begin
synthesising audio before the LLM finishes generating — the §6 1500 ms
p95 end-to-end budget is unreachable without streaming.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from verbio_engine.mouth.context import PhrasingContext
from verbio_engine.mouth.persona import ModeratorPersona


@dataclass(frozen=True)
class MouthRequest:
    """Input to the mouth — pure data, never carries `SessionState`."""

    action: str  # DecisionAction; left as str to avoid import cycle
    persona: ModeratorPersona
    context: PhrasingContext


@dataclass(frozen=True)
class MouthChunk:
    """One streamed piece of the moderator's utterance.

    `from_fallback` lets the orchestrator set `llm_fallback=true` on the
    decision per brief §8.4 without inspecting which implementation it
    called. Default False; TemplatedMouth and the DeepSeekMouth's
    fallback path both set it to True on every chunk they emit.
    """

    text: str
    is_final: bool = False
    from_fallback: bool = False


@runtime_checkable
class MouthClient(Protocol):
    """The mouth-layer contract per brief §8.1.

    Implementations must:
      - never reach back into application state (no DB / cache lookups);
      - emit at most one sentence's worth of output (the caller enforces
        with a wall-clock timeout + post-processing);
      - signal completion with a final chunk where `is_final=True`.
    """

    def phrase(self, request: MouthRequest) -> AsyncIterator[MouthChunk]:
        """Yield text chunks for the given request."""
        ...
