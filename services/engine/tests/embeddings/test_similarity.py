"""Tests for `cosine_similarity`."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, strategies as st

from verbio_engine.embeddings import cosine_similarity


class TestCosineSimilarityBasics:
    def test_identical_unit_vectors_score_1(self) -> None:
        v = [1.0, 0.0, 0.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_0(self) -> None:
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_antiparallel_vectors_score_negative_1(self) -> None:
        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_scale_invariance(self) -> None:
        a = [1.0, 2.0, 3.0]
        b = [10.0, 20.0, 30.0]
        assert cosine_similarity(a, b) == pytest.approx(1.0)

    def test_zero_vector_returns_zero(self) -> None:
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert cosine_similarity(a, b) == 0.0
        assert cosine_similarity(b, a) == 0.0

    def test_both_empty_returns_zero(self) -> None:
        assert cosine_similarity([], []) == 0.0


class TestCosineSimilarityErrors:
    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="length mismatch"):
            cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_one_empty_one_nonempty_raises(self) -> None:
        with pytest.raises(ValueError, match="length mismatch"):
            cosine_similarity([], [1.0])


class TestCosineSimilarityProperties:
    @given(
        st.lists(
            st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=64,
        )
    )
    def test_self_similarity_is_one_for_nonzero(self, v: list[float]) -> None:
        norm_sq = sum(x * x for x in v)
        if norm_sq == 0.0:
            assert cosine_similarity(v, v) == 0.0
        else:
            assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-9)

    @given(
        st.lists(
            st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=64,
        ),
        st.lists(
            st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=64,
        ),
    )
    def test_result_in_bounds(self, a: list[float], b: list[float]) -> None:
        # Match lengths so we exercise the math path, not the error path.
        n = min(len(a), len(b))
        a = a[:n]
        b = b[:n]
        sim = cosine_similarity(a, b)
        assert -1.0 - 1e-9 <= sim <= 1.0 + 1e-9
        assert not math.isnan(sim)

    @given(
        st.lists(
            st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=32,
        ),
        st.lists(
            st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=32,
        ),
    )
    def test_symmetric(self, a: list[float], b: list[float]) -> None:
        n = min(len(a), len(b))
        a = a[:n]
        b = b[:n]
        assert cosine_similarity(a, b) == pytest.approx(cosine_similarity(b, a))
