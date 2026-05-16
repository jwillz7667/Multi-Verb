"""Persistence layer for verbio-engine.

SQLAlchemy 2.0 async + asyncpg. The Alembic CLI uses psycopg2 for
introspection; runtime queries go through asyncpg via an async
`AsyncEngine`.

Public surface:
  - `Base`            : declarative base for all ORM models
  - `Session`         : sessions table model
  - `Participant`    : participants table model
  - `Utterance`       : utterances table model
  - `create_engine`   : async engine factory
  - `session_factory` : async sessionmaker factory
  - `UtteranceRepo`   : write/read API for utterances

Anything outside this barrel is internal — consumers depend only on
the public surface so the implementation can move without ripples.
"""

from verbio_engine.persistence.base import Base, metadata
from verbio_engine.persistence.engine import (
    create_engine,
    create_session_factory,
    dispose_engine,
)
from verbio_engine.persistence.models import Participant, Session, Utterance
from verbio_engine.persistence.repositories.utterances import (
    UtteranceInsert,
    UtteranceRepo,
)

__all__ = [
    "Base",
    "Participant",
    "Session",
    "Utterance",
    "UtteranceInsert",
    "UtteranceRepo",
    "create_engine",
    "create_session_factory",
    "dispose_engine",
    "metadata",
]
