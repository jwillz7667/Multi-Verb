"""`ModeratorPersona` — per-study persona consumed by the mouth + TTS layers.

Stored on `studies.moderator_persona` and snapshotted into
`sessions.config_snapshot` at session start (brief §7.5). Phase 4 is the
first consumer; until now the runtime treated the persona as an opaque
blob (`dict[str, Any]`).

Consumed fields:
  - `style_prompt` — prepended to the §8.2 system message verbatim.
  - `tone` + `formality` — advisory tone hints inside the prompt JSON.
  - `voice_provider` + `voice_id` — TTS voice selection (P4 L3+).

`extra="forbid"` so a typo'd persona key surfaces at session start,
not silently as a missing prompt clause; `frozen=True` so the runtime
can pass it around without defensive copies.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PersonaFormality = Literal["casual", "neutral", "formal"]
"""How conversational the moderator sounds; advisory tone hint."""

PersonaTone = Literal["warm", "neutral", "professional"]
"""Affective register; shapes the `tone` constraint in the prompt JSON."""

VoiceProvider = Literal["cartesia", "elevenlabs"]
"""Which TTS provider's voice library this persona's `voice_id` belongs to."""


class ModeratorPersona(BaseModel):
    """Frozen persona configuration for one study."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    style_prompt: str = Field(
        ...,
        description=(
            "Persona-flavored prefix prepended to the §8.2 system message. "
            "Keep short (1-2 sentences). Avoid prescribing what the "
            "moderator says — that is the engine's job, not the persona."
        ),
        min_length=1,
        max_length=500,
    )
    tone: PersonaTone = Field(
        default="warm",
        description="Base affective register; combined with per-action hints.",
    )
    formality: PersonaFormality = Field(
        default="neutral",
        description="How conversational the phrasing should feel.",
    )
    voice_provider: VoiceProvider = Field(
        default="cartesia",
        description="TTS provider whose voice library owns `voice_id`.",
    )
    voice_id: str = Field(
        ...,
        min_length=1,
        description="Provider-specific voice id; picked from the curated library.",
    )
