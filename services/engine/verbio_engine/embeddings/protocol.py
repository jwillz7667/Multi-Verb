"""`EmbeddingProvider` Protocol — what the state store depends on.

The rules engine never embeds text inline; predicates are pure functions
of `SessionState`. The state store is the *only* component that calls
an `EmbeddingProvider`, and it does so on inbound transcript updates
and at study load — exactly the two places where new text enters the
system. By the time a rule predicate runs, the relevant vectors are
already sitting on `SessionState` as `list[float]`.

The protocol is intentionally narrow:
  * `embed_one` for a single string — used on the study prompt at
    session load and on each new rolling-transcript window.
  * `embed_many` for a batch — used at warm-start (replay) and any
    future place that wants to amortise round-trips.
  * `dim` so the state store can validate vector shape against the
    rule that consumes it.
  * `model_name` so the snapshot can record which model produced
    these vectors and downstream replay refuses to compare across
    model families.

`EmbeddingError` is the only exception type that crosses the boundary
upward. Concrete providers wrap transport errors (httpx, openai SDK
errors, etc.) in `EmbeddingError` so the state store's error handling
is provider-agnostic.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


class EmbeddingError(Exception):
    """Raised when a provider cannot return an embedding.

    Wraps the underlying transport / API error in `__cause__` so the
    state store logs a clean message ("DeepSeek embed timeout") while
    keeping the full stack trace for debugging. The state store catches
    this, marks the affected embedding field as `None` on the snapshot,
    and lets the rule's "no embedding yet" branch handle it.
    """


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Async embedding source. One per process, injected at boot.

    All methods are coroutines because the only realistic backends
    (OpenAI, DeepSeek, self-hosted) are network calls. The mock test
    double also exposes async methods so tests exercise the same
    awaiting code paths as production.
    """

    dim: int
    """Vector dimensionality. The state store validates incoming
    vectors against this value before storing them.
    """

    model_name: str
    """Identifier for the embedding model, e.g. "text-embedding-3-small".
    Persisted alongside snapshots so cross-model comparisons can be
    refused at replay time.
    """

    async def embed_one(self, text: str) -> list[float]:
        """Embed a single string. Always returns a vector of length `dim`.

        Empty `text` is the caller's responsibility — providers may
        return a zero-vector or raise; both are acceptable. The state
        store skips embedding for empty transcripts upstream.
        """
        ...

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch. Returns vectors in the same order as `texts`.

        Concrete providers should chunk internally if `texts` exceeds
        the backend's per-request limit; the state store treats this
        as a single logical operation.
        """
        ...
