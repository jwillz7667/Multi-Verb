"""Persistence layer for verbio-engine.

SQLAlchemy 2.0 async + asyncpg. The Alembic CLI uses psycopg2 for
introspection; runtime queries go through asyncpg via an async
`AsyncEngine`.

Public surface:
  - `Base`                  : declarative base for all ORM models
  - `Session`               : sessions table model
  - `Participant`           : participants table model
  - `Utterance`             : utterances table model
  - `StateSnapshot`         : state_snapshots table model (P2 L3)
  - `create_engine`         : async engine factory
  - `session_factory`       : async sessionmaker factory
  - `SessionRepo`           : lifecycle API for sessions
  - `ParticipantRepo`       : join/leave API for participants
  - `ParticipantJoin`       : input record for participant upsert
  - `UtteranceRepo`         : write/read API for utterances
  - `UtteranceInsert`       : input record for utterance insert
  - `StateSnapshotRepo`     : write API for state snapshots (P2 L3)
  - `StateSnapshotInsert`   : input record for state-snapshot insert

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
    Participant,
    Session,
    StateSnapshot,
    Utterance,
)
from verbio_engine.persistence.repositories.participants import (
    ParticipantJoin,
    ParticipantRepo,
)
from verbio_engine.persistence.repositories.sessions import SessionRepo
from verbio_engine.persistence.repositories.state_snapshots import (
    StateSnapshotInsert,
    StateSnapshotRepo,
)
from verbio_engine.persistence.repositories.utterances import (
    UtteranceInsert,
    UtteranceRepo,
)

__all__ = [
    "Base",
    "Participant",
    "ParticipantJoin",
    "ParticipantRepo",
    "Session",
    "SessionRepo",
    "StateSnapshot",
    "StateSnapshotInsert",
    "StateSnapshotRepo",
    "Utterance",
    "UtteranceInsert",
    "UtteranceRepo",
    "create_engine",
    "create_session_factory",
    "dispose_engine",
    "metadata",
]
