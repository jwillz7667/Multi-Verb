"""Redis-Streams command bus — engine consumer side.

The web service XADDs each `ResearcherCommand` (brief §5.4) onto a
per-session Redis Stream (`verbio:commands:{session_id}`). The
engine's per-session tick loop calls `drain(session_id)` once per tick
to consume any new entries. Returned commands are then handed to the
listener (P5 L3+) which translates them into `ModeratorDecision`s and
persists them to the `researcher_actions` audit table.

Why Redis Streams instead of pub/sub
------------------------------------

Pub/sub is fire-and-forget — a message published while the engine is
restarting is gone forever, which would silently drop researcher
commands. Streams persist entries until trimmed and let the consumer
resume from a cursor, so a worker restart can replay any commands that
landed during the downtime. The producer XADDs with `MAXLEN ~ 1000` so
an unconsumed stream stays bounded if the engine is offline for an
extended period; replay-on-restart is bounded by the same cap.

Consumer model
--------------

Single-consumer-per-session means we don't need XREADGROUP / XACK; one
in-memory cursor per session is sufficient and avoids the per-tick
round-trip to ack. Idempotency at the persistence layer
(`ResearcherActionRepo` uses `command_id` as the row PK) handles the
duplicates that a "resume from 0" restart would produce.

Failure semantics
-----------------

Any Redis-side failure during `drain` is logged and yields an empty
list. The tick loop must never crash because Redis is degraded — the
moderator stays in its current state (silent by default) and the next
drain will pick up whatever the stream still holds.
"""

from __future__ import annotations

import json
import uuid
from typing import Protocol, runtime_checkable

from redis import asyncio as redis_async

from verbio_engine.domain.command import ResearcherCommand
from verbio_engine.logging import get_logger

log = get_logger(__name__)

_STREAM_PREFIX = "verbio:commands:"
"""Per-session Redis Stream key prefix. Must match the web producer."""

_DEFAULT_DRAIN_COUNT = 64
"""XREAD COUNT cap per drain.

Generous enough that a normal burst of mid-tick commands (researcher
mashing a button, sliding the quietness budget) lands in one call.
Anything beyond the cap is read on the next tick — drift over a sustained
burst is at most `interval_sec` per excess command.
"""

_DEFAULT_BLOCK_MS = 0
"""XREAD BLOCK milliseconds.

`0` = non-blocking; XREAD returns immediately if no new entries are
available. The tick scheduler owns the cadence, so we never want the
Redis read to block past `now()`.
"""

_INITIAL_CURSOR = b"0"
"""Resume from the start of the stream on first drain after process boot.

A worker restart replays whatever the stream still holds (bounded by
the producer's `MAXLEN ~ 1000`); duplicates are deduplicated by the
audit-table PK so this can't accidentally double-act on a command.
"""


def commands_stream_key(session_id: uuid.UUID) -> str:
    """Canonical Redis Stream key for one session's command bus.

    Single helper so engine and web can't drift on naming. Web mirrors
    this in `lib/redis.ts`.
    """
    return f"{_STREAM_PREFIX}{session_id}"


@runtime_checkable
class CommandBus(Protocol):
    """Structural type for `SessionRuntime` to depend on.

    Lets the runtime accept either the live Redis bus or a no-op /
    in-memory fake without inheriting from a base class.
    """

    async def drain(self, session_id: uuid.UUID) -> list[ResearcherCommand]: ...

    async def aclose(self) -> None: ...


class RedisCommandStreamBus:
    """Per-worker Redis Streams consumer.

    One instance shared across every session running in this worker;
    each `drain(session_id)` call multiplexes over the same connection
    pool. The in-memory cursor map (`session_id → last_seen_entry_id`)
    advances on every successful read so the next call only returns
    newer entries.
    """

    __slots__ = ("_block_ms", "_client", "_cursors", "_max_per_drain", "_url")

    def __init__(
        self,
        url: str,
        *,
        max_per_drain: int = _DEFAULT_DRAIN_COUNT,
        block_ms: int = _DEFAULT_BLOCK_MS,
    ) -> None:
        """Store the connection URL; connection is opened lazily.

        `from_url` itself does no I/O — the first XREAD opens the socket.
        Keeps the worker startup path light when Redis is unreachable.
        """
        self._url = url
        self._max_per_drain = max_per_drain
        self._block_ms = block_ms
        # `decode_responses=False` because XREAD returns bytes for both
        # entry ids and field values; we decode JSON ourselves.
        self._client: redis_async.Redis = redis_async.Redis.from_url(
            url,
            decode_responses=False,
        )
        self._cursors: dict[uuid.UUID, bytes] = {}

    async def drain(self, session_id: uuid.UUID) -> list[ResearcherCommand]:
        """Read all new commands for this session since the last cursor.

        Returns the typed commands in stream order. Advances the cursor
        even when entries fail to decode — a malformed entry cannot be
        rolled back and would otherwise block the per-session bus.

        Returns an empty list on Redis errors so the tick loop continues.
        The moderator stays silent / in its current state until the next
        successful drain.
        """
        key = commands_stream_key(session_id).encode("utf-8")
        cursor = self._cursors.get(session_id, _INITIAL_CURSOR)
        try:
            response = await self._client.xread(
                {key: cursor},
                count=self._max_per_drain,
                block=self._block_ms,
            )
        except Exception as exc:
            log.warning(
                "commands.drain_failed",
                session_id=str(session_id),
                error=str(exc),
            )
            return []

        if not response:
            return []

        commands: list[ResearcherCommand] = []
        for _stream_key, entries in response:
            for entry_id, fields in entries:
                # Advance the cursor unconditionally — a bad entry can't
                # be re-read, and refusing to advance would deadlock the
                # stream on the first malformed message.
                self._cursors[session_id] = entry_id
                decoded = _decode_entry(fields, entry_id=entry_id, session_id=session_id)
                if decoded is not None:
                    commands.append(decoded)
        return commands

    async def aclose(self) -> None:
        """Tear down the connection pool. Safe to call multiple times."""
        try:
            await self._client.aclose()
        except Exception as exc:
            log.debug("commands.close_warning", error=str(exc))


class NullCommandBus:
    """No-op bus for tests / unconfigured environments.

    Same surface as `RedisCommandStreamBus` so the runtime doesn't branch
    on bus type. Returns an empty list from every drain — the engine
    stays in shadow mode for researcher commands while still running the
    rules path normally.
    """

    async def drain(self, session_id: uuid.UUID) -> list[ResearcherCommand]:
        _ = session_id
        return []

    async def aclose(self) -> None:
        return None


def _decode_entry(
    fields: dict[bytes, bytes],
    *,
    entry_id: bytes,
    session_id: uuid.UUID,
) -> ResearcherCommand | None:
    """Decode one XREAD entry into a typed `ResearcherCommand`.

    Wire convention: the producer XADDs a single field named `data` with
    the JSON-encoded command body. Anything else (missing `data`, bad
    JSON, schema-validation failure) is logged with the entry id so the
    operator can correlate to the producer log, then skipped.
    """
    payload = fields.get(b"data")
    if payload is None:
        log.warning(
            "commands.entry_missing_data_field",
            session_id=str(session_id),
            entry_id=entry_id.decode("utf-8", errors="replace"),
            keys=[k.decode("utf-8", errors="replace") for k in fields],
        )
        return None
    try:
        body = json.loads(payload)
    except json.JSONDecodeError as exc:
        log.warning(
            "commands.entry_invalid_json",
            session_id=str(session_id),
            entry_id=entry_id.decode("utf-8", errors="replace"),
            error=str(exc),
        )
        return None
    try:
        return ResearcherCommand.model_validate(body)
    except Exception as exc:
        log.warning(
            "commands.entry_validation_failed",
            session_id=str(session_id),
            entry_id=entry_id.decode("utf-8", errors="replace"),
            error=str(exc),
        )
        return None
