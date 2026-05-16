"""`cosine_similarity` — pure helper for predicate vector comparisons.

Embeddings live on `SessionState` as plain `list[float]` (Pydantic-
serialisable, JSON-round-trippable for replay), not numpy arrays. The
state store may have produced them with numpy upstream, but by the
time a rule predicate sees them they're already in list form.

This helper is hand-rolled in pure Python rather than `numpy.dot /
linalg.norm` for two reasons:
  * Predicates are called from the 2 Hz tick loop and we'd rather not
    pay numpy's import-time cost in the rules layer.
  * Vectors are at most ~3000 floats (OpenAI text-embedding-3-large
    dim); a single dot-product in Python is well under a millisecond
    and the resolver's overall budget is comfortable.

Returns a value in [-1.0, 1.0]. Zero-length vectors collapse to 0.0
rather than raising — a fresh session whose embedding hasn't been
populated yet should not crash the tick loop; the rule predicate
above this helper decides whether "no embedding yet" means "don't
fire" or "fire conservatively".
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length real vectors.

    Raises `ValueError` on length mismatch — that is a programming
    error (two embeddings from different models being compared) and
    must surface loudly, not return a misleading zero.
    """
    if len(a) != len(b):
        msg = f"cosine_similarity: length mismatch ({len(a)} vs {len(b)})"
        raise ValueError(msg)

    if not a:
        # Both empty — undefined mathematically; return 0 so callers
        # can treat "no signal" as "no match" without a special case.
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    # Clamp to [-1, 1]: the mathematical bound is exact, but float ops
    # can push the result a few ULPs past either side (Hypothesis
    # surfaces this with tiny-magnitude vectors). Callers downstream
    # use the value as a threshold input and rely on the documented
    # range, so the clamp belongs here rather than at every callsite.
    raw = dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
    return max(-1.0, min(1.0, raw))
