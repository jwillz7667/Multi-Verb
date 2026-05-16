"""Domain types — canonical shapes used everywhere in the engine.

Pydantic is the single source of truth (brief §5). JSON Schema is generated
from these models by `verbio_engine.schema_export`, and the TypeScript types
consumed by `apps/web` are generated from that JSON Schema. The shapes must
not drift between languages; CI gates on it.
"""

from verbio_engine.domain.budget import QuietnessBudget
from verbio_engine.domain.command import ResearcherCommand, ResearcherCommandType
from verbio_engine.domain.decision import (
    DecisionAction,
    DecisionSource,
    ModeratorDecision,
)
from verbio_engine.domain.evaluation import RuleEvaluation
from verbio_engine.domain.participant import (
    ParticipantFlags,
    ParticipantState,
    UtteranceRef,
)
from verbio_engine.domain.session_state import SessionState

__all__ = [
    "DecisionAction",
    "DecisionSource",
    "ModeratorDecision",
    "ParticipantFlags",
    "ParticipantState",
    "QuietnessBudget",
    "ResearcherCommand",
    "ResearcherCommandType",
    "RuleEvaluation",
    "SessionState",
    "UtteranceRef",
]
