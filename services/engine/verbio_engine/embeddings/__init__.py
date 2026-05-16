"""`verbio_engine.embeddings` — text embedding abstraction for rule predicates.

The rules engine never embeds text inline (predicates are pure functions
of state). Embeddings are pre-computed by the state store on inbound
transcript updates and on study load; rules read the floats from
SessionState and compare via `cosine_similarity`.

Public surface:
  * `EmbeddingProvider` Protocol — what the state store depends on.
  * `EmbeddingError` — typed exception for upstream failures.
  * `MockEmbeddingProvider` — deterministic test double.
  * `cosine_similarity` — pure function over two float vectors.

Concrete providers (OpenAI, etc.) plug in at process boot from
`Settings`; the state store accepts the protocol.
"""

from verbio_engine.embeddings.mock import MockEmbeddingProvider
from verbio_engine.embeddings.protocol import EmbeddingError, EmbeddingProvider
from verbio_engine.embeddings.similarity import cosine_similarity

__all__ = [
    "EmbeddingError",
    "EmbeddingProvider",
    "MockEmbeddingProvider",
    "cosine_similarity",
]
