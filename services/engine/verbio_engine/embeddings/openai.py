"""`OpenAIEmbeddingProvider` — production embedder for `topic_drift`.

Backed by the `openai` SDK's async client. Defaults to
`text-embedding-3-small` (1536 dim) for cost / latency reasons — it is
plenty for the cosine-vs-prompt comparison the rule needs, and at
~$0.02 / 1M tokens it is effectively free at our throughput.

Errors are wrapped: any underlying `openai.APIError`, network failure,
or unexpected response shape becomes `EmbeddingError(... ) from exc`.
The state store catches that one type and degrades gracefully (leaves
the affected embedding field `None`, which the rule predicates already
handle as "don't fire").

Model dimensionality is *required* at construction — we do NOT auto-
probe the model. Two reasons:
  1. A surprise dim mismatch downstream is harder to debug than a
     loud constructor-time failure.
  2. The OpenAI SDK lets the caller pass `dimensions=` to truncate a
     larger model's vectors; if a study ever opts into that we want
     the truth in the call site, not inferred.

Defaults match the brief / Settings: 1536 for `text-embedding-3-small`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from verbio_engine.embeddings.protocol import EmbeddingError

if TYPE_CHECKING:
    from openai import AsyncOpenAI


# Map well-known OpenAI embedding models to their native dimensionality.
# Used only as the constructor's default when `dim` is omitted; explicit
# `dim=` always wins.
_DEFAULT_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbeddingProvider:
    """Async embedder that delegates to an injected `AsyncOpenAI` client.

    The client is constructed by the caller (so the same auth + retry
    config that powers the mouth layer is reused) and passed in. This
    keeps the provider testable: tests construct one with a stub
    client whose `.embeddings.create` returns a canned payload.
    """

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str = "text-embedding-3-small",
        dim: int | None = None,
    ) -> None:
        if dim is None:
            dim = _DEFAULT_DIMS.get(model)
        if dim is None:
            msg = f"OpenAIEmbeddingProvider: unknown model {model!r}; pass `dim=` explicitly."
            raise ValueError(msg)
        if dim <= 0:
            msg = f"OpenAIEmbeddingProvider: dim must be positive, got {dim}"
            raise ValueError(msg)

        self._client = client
        self.model_name = model
        self.dim = dim

    async def embed_one(self, text: str) -> list[float]:
        results = await self.embed_many([text])
        return results[0]

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        # OpenAI's `dimensions=` only works for -3-* models; passing it
        # for ada-002 errors out. Send it iff it would actually be
        # honoured — i.e., when we customised dim away from the model's
        # native size. Two branches rather than `**kwargs`-unpacking so
        # the SDK's overloaded signature stays statically resolvable.
        send_dim = self._should_send_dimensions()
        try:
            if send_dim:
                response = await self._client.embeddings.create(
                    model=self.model_name,
                    input=list(texts),
                    dimensions=self.dim,
                )
            else:
                response = await self._client.embeddings.create(
                    model=self.model_name,
                    input=list(texts),
                )
        except Exception as exc:
            msg = f"OpenAI embeddings call failed for model={self.model_name!r}"
            raise EmbeddingError(msg) from exc

        try:
            vectors = [item.embedding for item in response.data]
        except (AttributeError, TypeError) as exc:
            msg = f"OpenAI embeddings response missing .data[].embedding: {response!r}"
            raise EmbeddingError(msg) from exc

        if len(vectors) != len(texts):
            msg = f"OpenAI embeddings returned {len(vectors)} vectors for {len(texts)} inputs"
            raise EmbeddingError(msg)

        for i, v in enumerate(vectors):
            if len(v) != self.dim:
                msg = (
                    f"OpenAI embeddings dim mismatch at index {i}: "
                    f"expected {self.dim}, got {len(v)} (model={self.model_name!r})"
                )
                raise EmbeddingError(msg)

        return [list(v) for v in vectors]

    def _should_send_dimensions(self) -> bool:
        """True iff we should ask OpenAI to truncate to a non-native dim.

        Models that accept the param (`text-embedding-3-*`) return their
        native dim when the param is omitted; sending it equal to the
        native dim is harmless but noisy in logs. Models that don't
        accept it (`text-embedding-ada-002`) reject the request outright.
        """
        native = _DEFAULT_DIMS.get(self.model_name)
        if native is None or native == self.dim:
            return False
        return self.model_name != "text-embedding-ada-002"
