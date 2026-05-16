"""Unit tests for `EmbeddingCoordinator`.

Exercise the async glue between an `EmbeddingProvider` stub and a real
`StateStore`: study-prompt embedding, single-flight rolling re-embeds,
error degradation, and graceful shutdown via `aclose`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from verbio_engine.embeddings import EmbeddingCoordinator, EmbeddingError
from verbio_engine.state import StateStore
from verbio_engine.state.events import UtteranceFinalEvent


class _StubProvider:
    """Programmable EmbeddingProvider stub.

    `embed_one` returns the next canned vector and records the input.
    `raise_next` makes the next call raise `EmbeddingError`. When
    `use_gate` is True each call parks until the next `release()`; tests
    use this to stage bursts of re-embed requests while one is in flight.
    """

    def __init__(
        self,
        *,
        dim: int = 4,
        model_name: str = "stub-embed-v1",
    ) -> None:
        self.dim = dim
        self.model_name = model_name
        self.canned: list[list[float]] = []
        self.calls: list[str] = []
        self.raise_next: bool = False
        self.use_gate = False
        # Each pending call owns a per-call Event so the test can
        # release them one at a time without racing on a shared flag.
        self._pending_gates: list[asyncio.Event] = []

    async def embed_one(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.use_gate:
            gate = asyncio.Event()
            self._pending_gates.append(gate)
            await gate.wait()
        if self.raise_next:
            self.raise_next = False
            msg = "stub failure"
            raise EmbeddingError(msg)
        if not self.canned:
            return [0.0] * self.dim
        return self.canned.pop(0)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed_one(t) for t in texts]

    async def wait_for_call_count(self, n: int, *, timeout: float = 2.0) -> None:  # noqa: ASYNC109
        """Poll until `len(self.calls) >= n` or `timeout` elapses."""

        async def _wait() -> None:
            while len(self.calls) < n:  # noqa: ASYNC110
                await asyncio.sleep(0.001)

        await asyncio.wait_for(_wait(), timeout=timeout)

    def release_next(self) -> None:
        """Release the oldest still-parked call."""
        if self._pending_gates:
            self._pending_gates.pop(0).set()


def _make_store() -> StateStore:
    return StateStore(
        session_id=uuid.uuid4(),
        started_at=datetime(2026, 5, 16, 12, 0, tzinfo=UTC),
    )


def _record_final(store: StateStore, *, text: str, offset_sec: float) -> None:
    base = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    start = base + timedelta(seconds=offset_sec)
    end = start + timedelta(seconds=1.0)
    store.record(
        UtteranceFinalEvent(
            ts=end,
            utterance_id=str(uuid.uuid4()),
            participant_id="p1",
            text=text,
            start_ts=start,
            end_ts=end,
        )
    )
    store.advance_to(end + timedelta(milliseconds=10))


# ---------------------------------------------------------------- embed_study_prompt


class TestEmbedStudyPrompt:
    async def test_embeds_and_seeds_store(self) -> None:
        store = _make_store()
        provider = _StubProvider()
        provider.canned = [[0.5, 0.5, 0.5, 0.5]]
        coord = EmbeddingCoordinator(provider=provider, store=store)

        await coord.embed_study_prompt("how do you use the music app?")

        snapshot = store.advance_to(datetime(2026, 5, 16, 12, 0, 5, tzinfo=UTC))
        assert snapshot.study_prompt == "how do you use the music app?"
        assert snapshot.study_prompt_embedding == [0.5, 0.5, 0.5, 0.5]
        assert snapshot.embedding_model_name == "stub-embed-v1"
        assert provider.calls == ["how do you use the music app?"]

    async def test_blank_prompt_is_noop(self) -> None:
        store = _make_store()
        provider = _StubProvider()
        coord = EmbeddingCoordinator(provider=provider, store=store)

        await coord.embed_study_prompt("   \n\t  ")

        snapshot = store.advance_to(datetime(2026, 5, 16, 12, 0, 5, tzinfo=UTC))
        assert snapshot.study_prompt == ""
        assert snapshot.study_prompt_embedding is None
        assert provider.calls == []

    async def test_strips_whitespace(self) -> None:
        store = _make_store()
        provider = _StubProvider()
        provider.canned = [[0.1] * 4]
        coord = EmbeddingCoordinator(provider=provider, store=store)

        await coord.embed_study_prompt("  music app  ")

        assert provider.calls == ["music app"]
        snapshot = store.advance_to(datetime(2026, 5, 16, 12, 0, 5, tzinfo=UTC))
        assert snapshot.study_prompt == "music app"

    async def test_provider_failure_leaves_store_untouched(self) -> None:
        store = _make_store()
        provider = _StubProvider()
        provider.raise_next = True
        coord = EmbeddingCoordinator(provider=provider, store=store)

        # Must not raise — coordinator swallows and logs.
        await coord.embed_study_prompt("music app")

        snapshot = store.advance_to(datetime(2026, 5, 16, 12, 0, 5, tzinfo=UTC))
        assert snapshot.study_prompt == ""
        assert snapshot.study_prompt_embedding is None


# ---------------------------------------------------------------- rolling re-embed


class TestRequestRollingReembed:
    async def test_single_request_embeds_rolling_text(self) -> None:
        store = _make_store()
        provider = _StubProvider()
        provider.canned = [[0.7, 0.0, 0.0, 0.0]]
        coord = EmbeddingCoordinator(provider=provider, store=store)

        _record_final(store, text="i love this music app", offset_sec=1.0)
        coord.request_rolling_re_embed()
        await coord.aclose()

        snapshot = store.advance_to(datetime(2026, 5, 16, 12, 0, 5, tzinfo=UTC))
        assert snapshot.rolling_transcript_30s_embedding == [0.7, 0.0, 0.0, 0.0]
        assert provider.calls == ["i love this music app"]

    async def test_empty_transcript_clears_cache_without_calling_provider(self) -> None:
        store = _make_store()
        provider = _StubProvider()
        coord = EmbeddingCoordinator(provider=provider, store=store)

        # Seed a stale vector to prove the clear actually happens.
        store.set_rolling_transcript_embedding([0.9] * 4, model_name="stub-embed-v1")

        coord.request_rolling_re_embed()
        await coord.aclose()

        snapshot = store.advance_to(datetime(2026, 5, 16, 12, 0, 5, tzinfo=UTC))
        assert snapshot.rolling_transcript_30s_embedding is None
        assert provider.calls == []

    async def test_burst_collapses_to_two_calls(self) -> None:
        """Single-flight pattern: many requests during one in-flight call
        collapse to at most one follow-up.
        """
        store = _make_store()
        provider = _StubProvider()
        provider.use_gate = True
        provider.canned = [[0.1] * 4, [0.2] * 4, [0.3] * 4]
        coord = EmbeddingCoordinator(provider=provider, store=store)

        _record_final(store, text="alpha", offset_sec=1.0)

        # First request → worker starts; first embed_one parks on its gate.
        coord.request_rolling_re_embed()
        await provider.wait_for_call_count(1)

        # Pile on five more requests while the first is in flight.
        _record_final(store, text="beta", offset_sec=2.0)
        for _ in range(5):
            coord.request_rolling_re_embed()

        # Release the first; the worker re-loops once for the pending bit.
        provider.release_next()
        await provider.wait_for_call_count(2)

        # Release the second so the worker can exit.
        provider.release_next()
        await coord.aclose()

        # Exactly two calls — first burst + one follow-up — not seven.
        assert len(provider.calls) == 2
        assert provider.calls[0] == "alpha"  # first call sees only "alpha"
        # Second call sees the full window (alpha + beta) — chronological.
        assert "alpha" in provider.calls[1]
        assert "beta" in provider.calls[1]

    async def test_provider_failure_clears_cache(self) -> None:
        store = _make_store()
        provider = _StubProvider()
        coord = EmbeddingCoordinator(provider=provider, store=store)

        # Seed a stale vector so we can observe the clear.
        store.set_rolling_transcript_embedding([0.9] * 4, model_name="stub-embed-v1")
        _record_final(store, text="alpha", offset_sec=1.0)

        provider.raise_next = True
        coord.request_rolling_re_embed()
        await coord.aclose()

        snapshot = store.advance_to(datetime(2026, 5, 16, 12, 0, 5, tzinfo=UTC))
        assert snapshot.rolling_transcript_30s_embedding is None

    async def test_recovers_after_failure(self) -> None:
        store = _make_store()
        provider = _StubProvider()
        coord = EmbeddingCoordinator(provider=provider, store=store)

        _record_final(store, text="alpha", offset_sec=1.0)

        # Fail once.
        provider.raise_next = True
        coord.request_rolling_re_embed()
        await coord.aclose()

        # Now a successful round.
        provider.canned = [[0.4] * 4]
        _record_final(store, text="beta", offset_sec=2.0)
        coord.request_rolling_re_embed()
        await coord.aclose()

        snapshot = store.advance_to(datetime(2026, 5, 16, 12, 0, 5, tzinfo=UTC))
        assert snapshot.rolling_transcript_30s_embedding == [0.4] * 4

    async def test_re_request_after_worker_exit_spawns_new_worker(self) -> None:
        """The first burst drains and the worker exits; the next request
        must spawn a fresh worker, not assume one is already running."""
        store = _make_store()
        provider = _StubProvider()
        provider.canned = [[0.1] * 4, [0.2] * 4]
        coord = EmbeddingCoordinator(provider=provider, store=store)

        _record_final(store, text="alpha", offset_sec=1.0)
        coord.request_rolling_re_embed()
        await coord.aclose()
        assert len(provider.calls) == 1

        # Worker has exited. Next request must run.
        _record_final(store, text="beta", offset_sec=2.0)
        coord.request_rolling_re_embed()
        await coord.aclose()
        assert len(provider.calls) == 2


# ---------------------------------------------------------------- aclose


class TestAclose:
    async def test_noop_when_never_requested(self) -> None:
        store = _make_store()
        provider = _StubProvider()
        coord = EmbeddingCoordinator(provider=provider, store=store)

        # Should return immediately without error.
        await coord.aclose()
        await coord.aclose()  # idempotent

    async def test_awaits_in_flight_embed(self) -> None:
        store = _make_store()
        provider = _StubProvider()
        provider.use_gate = True
        provider.canned = [[0.42] * 4]
        coord = EmbeddingCoordinator(provider=provider, store=store)

        _record_final(store, text="alpha", offset_sec=1.0)
        coord.request_rolling_re_embed()
        await provider.wait_for_call_count(1)

        # Start the close in parallel; it must wait for the in-flight call.
        closer = asyncio.create_task(coord.aclose())
        await asyncio.sleep(0)  # let closer park

        assert not closer.done()
        provider.release_next()
        await closer

        snapshot = store.advance_to(datetime(2026, 5, 16, 12, 0, 5, tzinfo=UTC))
        assert snapshot.rolling_transcript_30s_embedding == [0.42] * 4

    async def test_cancels_hung_worker_after_timeout(self) -> None:
        store = _make_store()
        provider = _StubProvider()
        provider.use_gate = True
        coord = EmbeddingCoordinator(provider=provider, store=store)

        _record_final(store, text="alpha", offset_sec=1.0)
        coord.request_rolling_re_embed()
        await provider.wait_for_call_count(1)

        # Don't release the gate; aclose must time out and cancel.
        await coord.aclose(timeout=0.05)

        # Worker reference cleared.
        assert coord._worker_task is None


# ---------------------------------------------------------------- model_name


def test_model_name_proxies_provider() -> None:
    store = _make_store()
    provider = _StubProvider(model_name="my-custom-embed")
    coord = EmbeddingCoordinator(provider=provider, store=store)
    assert coord.model_name == "my-custom-embed"


# ---------------------------------------------------------------- window param


class TestRollingWindow:
    async def test_window_filters_out_old_finals(self) -> None:
        store = _make_store()
        provider = _StubProvider()
        provider.canned = [[0.1] * 4]
        coord = EmbeddingCoordinator(
            provider=provider,
            store=store,
            rolling_window_sec=5.0,  # tiny window
        )

        # This final is 10s into the session, well before the 30s default
        # but inside the 5s custom window only if we advance close to it.
        _record_final(store, text="i love music apps", offset_sec=1.0)
        # Advance forward 20s so the 5s window excludes the segment.
        store.advance_to(datetime(2026, 5, 16, 12, 0, 21, tzinfo=UTC))

        coord.request_rolling_re_embed()
        await coord.aclose()

        # Empty window → no provider call.
        assert provider.calls == []
