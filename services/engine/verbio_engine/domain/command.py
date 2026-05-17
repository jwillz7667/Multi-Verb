"""`ResearcherCommand` — inbound from web → engine via Redis (brief §5.4).

The web service publishes commands onto a per-session Redis stream; the
engine drains the stream at the top of each tick and resolves the decision
from commands before consulting the rules engine.

Wire shape is intentionally faithful to brief §5.4: `command_type` is the
top-level discriminator and `payload` is an open `dict`. The typed payload
classes below pin the per-command field set and run as a post-init
validator — engine call sites that need typed access call
`<Payload>.model_validate(cmd.payload)` themselves.

Why not a Pydantic discriminated union directly? Two reasons:

  1. The brief shows `payload: dict  # command-specific` literally, and the
     web producer (TypeScript) serialises arbitrary JSON into the same
     field. Keeping the wire shape one-to-one with the brief means the
     generated TypeScript type lines up with what the web app actually
     sends.
  2. The web app issues commands well before the engine has decided which
     payload subtypes are required for every variant; relaxing the wire
     shape to a discriminated union now would force a co-ordinated
     redeploy each time a payload field is added. Validating an open
     dict against typed models leaves room for additive payload fields
     without churning the shared schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    PositiveInt,
    ValidationError,
    model_validator,
)

ResearcherCommandType = Literal[
    "force_prompt",
    "force_redirect",
    "force_summary",
    "whisper",
    "mute_moderator",
    "unmute_moderator",
    "pause_session",
    "resume_session",
    "set_quietness_budget",
    "flag_moment",
    "end_session",
]
"""Closed set of researcher actions (brief §5.4)."""


CommandPayload = dict[str, Any]
"""Raw wire payload — typed per command via the validators below."""


class _PayloadBase(BaseModel):
    """Shared config for typed payload variants: forbid unknown keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ForcePromptPayload(_PayloadBase):
    """`force_prompt` — researcher asks the moderator to prompt a participant.

    The mouth layer phrases the actual utterance; `prompt` is the seed
    intent ("ask Maria what she meant by 'too expensive'"). When
    `target_participant_id` is set the engine routes the resulting
    decision at that participant; otherwise the moderator addresses the
    group.
    """

    prompt: Annotated[str, Field(min_length=1, max_length=2000)]
    target_participant_id: UUID | None = None


class ForceRedirectPayload(_PayloadBase):
    """`force_redirect` — steer the discussion to a specific topic."""

    topic: Annotated[str, Field(min_length=1, max_length=2000)]


class ForceSummaryPayload(_PayloadBase):
    """`force_summary` — summarise the conversation so far.

    `focus` is optional; without it the mouth summarises the full
    discussion thread. With it the summary is scoped (e.g., "focus on
    the pricing pushback").
    """

    focus: Annotated[str, Field(min_length=1, max_length=2000)] | None = None


class WhisperPayload(_PayloadBase):
    """`whisper` — verbatim text bypassing the mouth layer.

    The engine pipes `text` straight to TTS, attributing the decision
    with `source="researcher_whisper"`. `target_participant_id` is the
    addressed participant (optional — null addresses the group).
    """

    text: Annotated[str, Field(min_length=1, max_length=2000)]
    target_participant_id: UUID | None = None


class MuteModeratorPayload(_PayloadBase):
    """`mute_moderator` — silence the moderator until explicitly unmuted."""


class UnmuteModeratorPayload(_PayloadBase):
    """`unmute_moderator` — re-enable the moderator after a mute."""


class PauseSessionPayload(_PayloadBase):
    """`pause_session` — halt the tick loop until resumed."""


class ResumeSessionPayload(_PayloadBase):
    """`resume_session` — un-pause a paused session."""


class SetQuietnessBudgetPayload(_PayloadBase):
    """`set_quietness_budget` — live-adjust the moderator's speech budget.

    At least one field must be set. Field semantics mirror
    `QuietnessBudget` (brief §7.4) so the engine can patch the live
    budget without re-deriving keys. Unset fields leave the current
    value untouched.
    """

    max_utterances_per_10min: PositiveInt | None = None
    min_seconds_between_utterances: Annotated[NonNegativeFloat, Field(ge=0.0)] | None = None

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> Self:
        if self.max_utterances_per_10min is None and self.min_seconds_between_utterances is None:
            msg = (
                "set_quietness_budget payload must set at least one of "
                "max_utterances_per_10min, min_seconds_between_utterances"
            )
            raise ValueError(msg)
        return self


class FlagMomentPayload(_PayloadBase):
    """`flag_moment` — bookmark the current tick for post-session replay."""

    note: Annotated[str, Field(min_length=1, max_length=1000)] | None = None


class EndSessionPayload(_PayloadBase):
    """`end_session` — cleanly terminate the session."""

    reason: Annotated[str, Field(min_length=1, max_length=500)] | None = None


_PAYLOAD_FOR_COMMAND: dict[ResearcherCommandType, type[_PayloadBase]] = {
    "force_prompt": ForcePromptPayload,
    "force_redirect": ForceRedirectPayload,
    "force_summary": ForceSummaryPayload,
    "whisper": WhisperPayload,
    "mute_moderator": MuteModeratorPayload,
    "unmute_moderator": UnmuteModeratorPayload,
    "pause_session": PauseSessionPayload,
    "resume_session": ResumeSessionPayload,
    "set_quietness_budget": SetQuietnessBudgetPayload,
    "flag_moment": FlagMomentPayload,
    "end_session": EndSessionPayload,
}
"""command_type → typed payload class.

Kept as a module-level mapping so the validator on `ResearcherCommand`
stays a one-liner lookup and so external code (engine command handlers,
tests) can pick the right model without restating the table.
"""


class ResearcherCommand(BaseModel):
    """A single researcher-issued command targeting one session.

    Wire shape matches brief §5.4 verbatim. The post-init validator
    asserts `payload` conforms to the typed model for `command_type`;
    `payload` itself stays an open `dict[str, Any]` to keep the JSON
    Schema (and thus the generated TypeScript) one-to-one with what the
    web producer sends.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: UUID
    session_id: UUID
    researcher_id: str
    issued_at: datetime
    command_type: ResearcherCommandType
    payload: CommandPayload = Field(
        default_factory=dict,
        description=(
            "Command-specific JSON payload; validated against the typed model " "for command_type."
        ),
    )

    @model_validator(mode="after")
    def _validate_payload_against_command_type(self) -> Self:
        payload_cls = _PAYLOAD_FOR_COMMAND[self.command_type]
        try:
            payload_cls.model_validate(self.payload)
        except ValidationError as exc:
            # Re-raise as ValueError so Pydantic wraps it in this model's
            # ValidationError envelope (with the right path), preserving
            # the per-field detail from the inner validation.
            msg = f"invalid payload for command_type={self.command_type!r}: {exc.errors()}"
            raise ValueError(msg) from exc
        return self
