"""`EmbeddingCoordinator` — async glue between EmbeddingProvider and StateStore.

The state store is sync at `advance_to` time; the EmbeddingProvider is
async. The coordinator owns the bridge: it calls the provider in the
background, then feeds results back into the store via the existing
`set_study_prompt` / `set_rolling_transcript_embedding` setters.

Two responsibilities:

1. **Study prompt** — embedded once per session via `embed_study_prompt`.
   The caller awaits this (it's a one-shot at session start and the
   `topic_drift` rule has no signal until it completes).

2. **Rolling 30s transcript** — re-embedded on each utterance-final via
   `request_rolling_re_embed`. Fire-and-forget single-flight: at most one
   embed call is in flight; if more arrive while one is running they
   collapse into a single follow-up call. This is the right shape because
   the rolling transcript barely changes between two finals 1s apart, and
   the natural OpenAI round-trip latency (~150ms) provides natural
   debouncing.

Failure handling: any `EmbeddingError` (or unexpected exception) clears
the rolling cache via `set_rolling_transcript_embedding(None, ...)`. The
`topic_drift` rule then sees `rolling_transcript_30s_embedding is None`
and degrades to "don't fire" — exactly the silence-bias we want when the
provider is flaky. Errors are logged at the coordinator level so the
runtime layer doesn't have to plumb them.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from verbio_engine.embeddings.protocol import EmbeddingError
from verbio_engine.logging import get_logger

if TYPE_CHECKING:
    from verbio_engine.embeddings.protocol import EmbeddingProvider
    from verbio_engine.state.store import StateStore

log = get_logger(__name__)

_DEFAULT_ROLLING_WINDOW_SEC = 30.0


class EmbeddingCoordinator:
    """Bridges async EmbeddingProvider calls into the sync state store."""

    def __init__(
        self,
        *,
        provider: EmbeddingProvider,
        store: StateStore,
        rolling_window_sec: float = _DEFAULT_ROLLING_WINDOW_SEC,
    ) -> None:
        self._provider = provider
        self._store = store
        self._rolling_window_sec = rolling_window_sec

        # Single-flight machinery for rolling re-embeds.
        self._pending: bool = False
        self._worker_task: asyncio.Task[None] | None = None

    @property
    def model_name(self) -> str:
        return self._provider.model_name

    async def embed_study_prompt(self, prompt: str) -> None:
        """Embed the study prompt once and seed the state store.

        Blank prompts are tolerated: we skip the embed call and leave the
        store's defaults (`study_prompt=""`, `study_prompt_embedding=None`).
        The `topic_drift` rule guards on the None embedding and stays
        silent — same outcome as if the rule were disabled, but
        observable in the audit log.

        Errors do NOT raise; they log and leave the store untouched.
        Topic_drift won't fire without the prompt embedding, which is the
        right failure mode.
        """
        prompt = prompt.strip()
        if not prompt:
            log.info("embedding.study_prompt.skipped_blank")
            return
        try:
            vector = await self._provider.embed_one(prompt)
        except EmbeddingError:
            log.exception(
                "embedding.study_prompt.failed",
                model=self._provider.model_name,
            )
            return
        self._store.set_study_prompt(
            prompt,
            vector,
            model_name=self._provider.model_name,
        )
        log.info(
            "embedding.study_prompt.set",
            model=self._provider.model_name,
            dim=len(vector),
        )

    def request_rolling_re_embed(self) -> None:
        """Signal that the rolling transcript has changed; run a re-embed soon.

        Sync entry point — the runtime calls this from `persist_utterance`
        right after recording the UtteranceFinalEvent. Returns immediately;
        the actual embed runs on a background task.

        Single-flight: if a worker is already running, this just flips
        `_pending` so the worker re-loops; if not, it spawns one. The
        worker loop drains `_pending` each iteration, so a burst of N
        requests collapses to at most 2 embed calls (one in flight when
        the burst arrives + one follow-up).
        """
        self._pending = True
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def _worker_loop(self) -> None:
        """Drain `_pending` until quiet, embedding once per pass."""
        while self._pending:
            self._pending = False
            text = self._store.rolling_transcript_text(window_sec=self._rolling_window_sec)
            if not text.strip():
                # Nothing finalized in window — clear stale cache so the
                # rule sees "no signal" instead of an outdated vector.
                self._store.set_rolling_transcript_embedding(
                    None,
                    model_name=self._provider.model_name,
                )
                continue
            try:
                vector = await self._provider.embed_one(text)
            except EmbeddingError:
                log.exception(
                    "embedding.rolling.failed",
                    model=self._provider.model_name,
                    text_len=len(text),
                )
                self._store.set_rolling_transcript_embedding(
                    None,
                    model_name=self._provider.model_name,
                )
                continue
            self._store.set_rolling_transcript_embedding(
                vector,
                model_name=self._provider.model_name,
            )

    async def aclose(self, *, timeout: float = 5.0) -> None:  # noqa: ASYNC109
        """Await any in-flight re-embed so shutdown doesn't drop a write.

        Idempotent. Safe to call when nothing was ever requested. We do
        NOT clear `_pending` here — that would race with a freshly-spawned
        worker that hasn't yet entered its loop and would silently drop
        the request. The worker drains whatever's pending and exits;
        downstream code calling `aclose` is expected to stop submitting.

        If the worker is wedged on a hung provider, cancel after `timeout`
        rather than block shutdown indefinitely.
        """
        task = self._worker_task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except TimeoutError:
            log.warning("embedding.coordinator.shutdown_timeout", timeout=timeout)
            task.cancel()
        finally:
            self._worker_task = None
