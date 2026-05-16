"""`ResearcherCommand` — inbound from web → engine via Redis (brief §5.4).

The web service publishes commands onto a per-session Redis stream; the
engine drains the stream at the top of each tick and resolves the decision
from commands before consulting the rules engine.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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


# `payload` is command-specific; the discriminated union arrives with the
# researcher-controls work in Phase 5. For now it accepts any JSON object.
CommandPayload = dict[str, Any]


class ResearcherCommand(BaseModel):
    """A single researcher-issued command targeting one session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: UUID
    session_id: UUID
    researcher_id: str
    issued_at: datetime
    command_type: ResearcherCommandType
    payload: CommandPayload = Field(
        default_factory=dict,
        description="Command-specific JSON payload; typed per command_type in Phase 5.",
    )
