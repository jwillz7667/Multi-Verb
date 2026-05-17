"""Persistence layer for verbio-engine.

SQLAlchemy 2.0 async + asyncpg. The Alembic CLI uses psycopg2 for
introspection; runtime queries go through asyncpg via an async
`AsyncEngine`.

Public surface:
  - `Base`                  : declarative base for all ORM models
  - `Study`                 : studies table model (P3 L11)
  - `Session`               : sessions table model
  - `Participant`           : participants table model
  - `Utterance`             : utterances table model
  - `StateSnapshot`         : state_snapshots table model (P2 L3)
  - `Decision`              : decisions table model (P3 L9)
  - `RuleEvaluation`        : rule_evaluations table model (P3 L9)
  - `ResearcherAction`      : researcher_actions table model (P5 L1)
  - `SessionFlag`           : session_flags table model (P5 L1)
  - `create_engine`         : async engine factory
  - `session_factory`       : async sessionmaker factory
  - `SessionRepo`           : lifecycle API for sessions
  - `StudyRepo`             : lookup API for studies (P3 L11)
  - `StudyInsert`           : input record for study insert (P3 L11)
  - `ParticipantRepo`       : join/leave API for participants
  - `ParticipantJoin`       : input record for participant upsert
  - `UtteranceRepo`         : write/read API for utterances
  - `UtteranceInsert`       : input record for utterance insert
  - `StateSnapshotRepo`     : write API for state snapshots (P2 L3)
  - `StateSnapshotInsert`   : input record for state-snapshot insert
  - `DecisionRepo`          : write API for decisions (P3 L9)
  - `DecisionInsert`        : input record for decision insert
  - `RuleEvaluationRepo`    : write API for rule evaluations (P3 L9)
  - `RuleEvaluationInsert`  : input record for rule-evaluation insert
  - `ResearcherActionRepo`  : write API for researcher actions (P5 L2)
  - `ResearcherActionInsert`: input record for researcher-action insert

Anything outside this barrel is internal — consumers depend only on
the public surface so the implementation can move without ripples.
"""

from verbio_engine.persistence.base import Base, metadata
from verbio_engine.persistence.engine import (
    create_engine,
    create_session_factory,
    dispose_engine,
)
from verbio_engine.persistence.models import (
    Decision,
    Participant,
    ResearcherAction,
    RuleEvaluation,
    Session,
    SessionFlag,
    StateSnapshot,
    Study,
    Utterance,
)
from verbio_engine.persistence.repositories.decisions import (
    DecisionInsert,
    DecisionRepo,
)
from verbio_engine.persistence.repositories.participants import (
    ParticipantJoin,
    ParticipantRepo,
)
from verbio_engine.persistence.repositories.researcher_actions import (
    ResearcherActionInsert,
    ResearcherActionRepo,
)
from verbio_engine.persistence.repositories.rule_evaluations import (
    RuleEvaluationInsert,
    RuleEvaluationRepo,
)
from verbio_engine.persistence.repositories.sessions import SessionRepo
from verbio_engine.persistence.repositories.state_snapshots import (
    StateSnapshotInsert,
    StateSnapshotRepo,
)
from verbio_engine.persistence.repositories.studies import (
    StudyInsert,
    StudyRepo,
)
from verbio_engine.persistence.repositories.utterances import (
    UtteranceInsert,
    UtteranceRepo,
)

__all__ = [
    "Base",
    "Decision",
    "DecisionInsert",
    "DecisionRepo",
    "Participant",
    "ParticipantJoin",
    "ParticipantRepo",
    "ResearcherAction",
    "ResearcherActionInsert",
    "ResearcherActionRepo",
    "RuleEvaluation",
    "RuleEvaluationInsert",
    "RuleEvaluationRepo",
    "Session",
    "SessionFlag",
    "SessionRepo",
    "StateSnapshot",
    "StateSnapshotInsert",
    "StateSnapshotRepo",
    "Study",
    "StudyInsert",
    "StudyRepo",
    "Utterance",
    "UtteranceInsert",
    "UtteranceRepo",
    "create_engine",
    "create_session_factory",
    "dispose_engine",
    "metadata",
]
