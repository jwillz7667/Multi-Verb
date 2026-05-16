"""`ModeratorDecision` — the audit record for every tick (brief §5.2).

Persisted before execution. `stay_silent` decisions are persisted at the
same fidelity as spoken ones — this is what powers "why didn't it speak?".
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DecisionAction = Literal[
    "stay_silent",
    "prompt_participant",
    "redirect_topic",
    "summarize_thread",
    "request_clarification",
    "suggest_turn_taking",
    "close_session",
]
"""Closed set of decision actions the engine may take (brief §5.2)."""

DecisionSource = Literal["auto", "researcher_manual", "researcher_whisper"]
"""Who initiated the decision: the rules engine or a researcher override."""


# `dict[str, Any]` is the documented shape for `llm_prompt` (brief §5.2).
# The mouth-layer prompt schema is finalised in Phase 4; until then any
# JSON-encodable object is acceptable. Aliased for JSON Schema clarity.
LLMPromptPayload = dict[str, Any]


class ModeratorDecision(BaseModel):
    """Single decision record from one tick of the engine.

    Invariants enforced elsewhere (`verbio_engine.tick_loop`):
      * `was_executed` is True only for non-silent actions that completed
        before the latency budget elapsed.
      * `cooldown_until` is computed from the triggering rule's
        `default_cooldown_sec`; researcher overrides may extend it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: UUID
    session_id: UUID
    tick_id: Annotated[int, Field(ge=0, description="Monotonic per-session tick counter.")]
    timestamp: datetime

    action: DecisionAction
    target_participant_id: str | None = None

    # ----- Provenance ------------------------------------------------------
    source: DecisionSource
    triggering_rule: str | None = Field(
        default=None,
        description="Rule name when source='auto'; null otherwise.",
    )
    researcher_id: str | None = None
    researcher_hint: str | None = Field(
        default=None,
        description="Free-text guidance from a manual researcher command.",
    )

    # ----- Explanation -----------------------------------------------------
    reason_codes: list[str] = Field(
        default_factory=list,
        description="Structured codes, e.g. ['silence_gap_8s', 'p3_unheard_4min'].",
    )
    reason_human: str = Field(
        default="",
        description="Generated single-sentence rationale shown on the dashboard.",
    )
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0

    # ----- Suppression info (logged even when stay_silent) ----------------
    suppressed_by: list[str] = Field(
        default_factory=list,
        description="['quietness_budget', 'global_cooldown', 'lower_priority_won', ...].",
    )

    # ----- Execution -------------------------------------------------------
    was_executed: bool = False
    llm_prompt: LLMPromptPayload | None = Field(
        default=None,
        description="Verbatim mouth-layer input; tightened to a typed shape in Phase 4.",
    )
    llm_output: str | None = None
    tts_audio_url: str | None = None
    spoken_at: datetime | None = None
    cooldown_until: datetime
