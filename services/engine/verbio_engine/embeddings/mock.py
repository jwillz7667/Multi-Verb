"""`MockEmbeddingProvider` — deterministic test double.

Two requirements drive its shape:
  1. **Determinism.** The same input string must always produce the
     same vector, across processes and test runs. Property-based
     tests (Hypothesis) rely on this; so does the replay invariant.
  2. **Coarse semantic plausibility.** Tests that exercise topic-
     drift logic need "similar" inputs to score above "dissimilar"
     ones. Pure random hashing would make those tests flap. We use a
     bag-of-tokens hash so that strings sharing tokens land in the
     same direction in vector space.

We do NOT promise calibration against any real embedding model. Tests
that need "this text is closer to X than Y" should construct the
inputs accordingly (shared tokens vs disjoint tokens).

Default `dim` is 1536 to match OpenAI's `text-embedding-3-small`,
which is the L6 production choice. Tests can pass `dim=8` or similar
when they only care about shape semantics.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from verbio_engine.embeddings.protocol import EmbeddingProvider

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic, hash-based embedding provider for tests.

    Implementation: tokenise the input, hash each token to a seed,
    derive a deterministic float vector from the seed, sum per-token
    vectors, then L2-normalise the result. Shared tokens push two
    inputs in the same direction; disjoint tokens leave them roughly
    orthogonal.
    """

    def __init__(self, dim: int = 1536, model_name: str = "mock-embed-v1") -> None:
        if dim <= 0:
            msg = f"MockEmbeddingProvider: dim must be positive, got {dim}"
            raise ValueError(msg)
        self.dim = dim
        self.model_name = model_name

    async def embed_one(self, text: str) -> list[float]:
        return self._embed_sync(text)

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_sync(t) for t in texts]

    def _embed_sync(self, text: str) -> list[float]:
        tokens = _TOKEN_RE.findall(text.lower())
        if not tokens:
            # Empty input → zero vector. Cosine against a zero vector
            # collapses to 0.0 in our helper, which is the right default
            # for "no signal" comparisons.
            return [0.0] * self.dim

        vec = [0.0] * self.dim
        for tok in tokens:
            for i, v in enumerate(_token_vector(tok, self.dim)):
                vec[i] += v

        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]


def _token_vector(token: str, dim: int) -> list[float]:
    """Derive a deterministic, roughly-unit-length vector from a token.

    Uses sha256 of the token as a byte stream, repeatedly extending
    if `dim * 4` exceeds the 32-byte digest. Each 4-byte chunk maps to
    a signed float in [-1, 1]. Not cryptographic; just deterministic
    and well-distributed enough for tests.
    """
    needed = dim * 4
    buf = b""
    counter = 0
    while len(buf) < needed:
        h = hashlib.sha256(f"{token}|{counter}".encode()).digest()
        buf += h
        counter += 1

    out: list[float] = []
    for i in range(dim):
        chunk = buf[i * 4 : i * 4 + 4]
        # Map uint32 → [-1, 1]. Centre on 2^31, scale by 2^31.
        u = int.from_bytes(chunk, "big", signed=False)
        out.append((u - 2**31) / 2**31)
    return out
