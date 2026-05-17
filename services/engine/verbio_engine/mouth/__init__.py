"""`verbio_engine.mouth` — language generation layer (brief §8).

The load-bearing architectural commitment from the brief: the rules
engine decides *what* to say; the mouth layer decides *how* to phrase
it. The mouth NEVER sees full participant state, the rule logic, or
which rules fired — only the structured `ModeratorDecision`, the
study's `ModeratorPersona`, and a narrow `PhrasingContext`.

Public surface:
  - `ModeratorPersona` / `PersonaTone` / `PersonaFormality` /
    `VoiceProvider` — frozen persona config consumed by mouth + TTS.
  - `PhrasingContext` + `extract_phrasing_context` — typed seam that
    enforces the §8.1 "narrow context only" rule.
  - `MouthClient` (Protocol) + `MouthRequest` + `MouthChunk` — the
    contract every mouth implementation honours.
  - `build_prompt` — pure §8.2 JSON builder.
  - `format_template` + `NoFallbackTemplateError` — §8.4 fallback path
    for when the LLM call fails or exceeds the wall-clock budget.
  - `DeepSeekMouth` (P4 L2) — streaming production mouth.
  - `TemplatedMouth` (P4 L2) — fallback-only mouth, also used in
    shadow / smoke tests to avoid billing the LLM.

Direct cross-module imports across the seam are forbidden — anything
the runtime needs from the mouth package goes through this barrel.
"""

from verbio_engine.mouth.context import PhrasingContext, extract_phrasing_context
from verbio_engine.mouth.deepseek import (
    DEFAULT_BASE_URL,
    DEFAULT_FIRST_TOKEN_BUDGET_SEC,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    DeepSeekMouth,
)
from verbio_engine.mouth.persona import (
    ModeratorPersona,
    PersonaFormality,
    PersonaTone,
    VoiceProvider,
)
from verbio_engine.mouth.prompt_builder import build_prompt
from verbio_engine.mouth.protocol import MouthChunk, MouthClient, MouthRequest
from verbio_engine.mouth.templated import TemplatedMouth
from verbio_engine.mouth.templates import NoFallbackTemplateError, format_template

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_FIRST_TOKEN_BUDGET_SEC",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "DEFAULT_TEMPERATURE",
    "DeepSeekMouth",
    "ModeratorPersona",
    "MouthChunk",
    "MouthClient",
    "MouthRequest",
    "NoFallbackTemplateError",
    "PersonaFormality",
    "PersonaTone",
    "PhrasingContext",
    "TemplatedMouth",
    "VoiceProvider",
    "build_prompt",
    "extract_phrasing_context",
    "format_template",
]
