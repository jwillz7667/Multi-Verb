"""`verbio_engine.mouth` — language generation layer (brief §8).

The load-bearing architectural commitment from the brief: the rules
engine decides *what* to say; the mouth layer decides *how* to phrase
it. The mouth NEVER sees full participant state, the rule logic, or
which rules fired — only the structured `ModeratorDecision`, the
study's `ModeratorPersona`, and a narrow `PhrasingContext`.

Public surface (P4 L1):
  - `ModeratorPersona` / `PersonaTone` / `PersonaFormality` /
    `VoiceProvider` — frozen persona config consumed by mouth + TTS.
  - `PhrasingContext` + `extract_phrasing_context` — typed seam that
    enforces the §8.1 "narrow context only" rule.
  - `MouthClient` (Protocol) + `MouthRequest` + `MouthChunk` — the
    contract every mouth implementation honours.
  - `build_prompt` — pure §8.2 JSON builder.
  - `format_template` + `NoFallbackTemplateError` — §8.4 fallback path
    for when the LLM call fails or exceeds the wall-clock budget.

Network-bearing implementations (`DeepSeekMouth`, etc.) live in
sibling modules added in later layers and are wired in via this
barrel — direct cross-module imports across the seam are forbidden.
"""

from verbio_engine.mouth.context import PhrasingContext, extract_phrasing_context
from verbio_engine.mouth.persona import (
    ModeratorPersona,
    PersonaFormality,
    PersonaTone,
    VoiceProvider,
)
from verbio_engine.mouth.prompt_builder import build_prompt
from verbio_engine.mouth.protocol import MouthChunk, MouthClient, MouthRequest
from verbio_engine.mouth.templates import NoFallbackTemplateError, format_template

__all__ = [
    "ModeratorPersona",
    "MouthChunk",
    "MouthClient",
    "MouthRequest",
    "NoFallbackTemplateError",
    "PersonaFormality",
    "PersonaTone",
    "PhrasingContext",
    "VoiceProvider",
    "build_prompt",
    "extract_phrasing_context",
    "format_template",
]
