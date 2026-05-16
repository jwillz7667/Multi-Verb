"""Repository pattern for verbio-engine persistence.

Each repository wraps an `AsyncSession` and exposes the *minimum* API
the engine actually calls. We don't ship CRUD-by-default — every method
maps to a real engine use case so the surface stays tractable.

Public surface:
  - `UtteranceRepo`
  - `UtteranceInsert`
"""

from verbio_engine.persistence.repositories.utterances import (
    UtteranceInsert,
    UtteranceRepo,
)

__all__ = ["UtteranceInsert", "UtteranceRepo"]
