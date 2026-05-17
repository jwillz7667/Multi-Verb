"""Researcher command bus — web → engine over Redis Streams (P5 L2).

The web service publishes commands to a per-session Redis Stream
(`verbio:commands:{session_id}`) via XADD; the engine's per-session
tick loop drains the stream at the top of each tick via XREAD,
validates each entry against the typed `ResearcherCommand` model, and
persists the audit row before deciding whether to act (brief §6 step 2).

Public surface:
  - `CommandBus`              : structural type the runtime depends on.
  - `RedisCommandStreamBus`   : Redis Streams XREAD consumer.
  - `NullCommandBus`          : no-op for tests / unconfigured envs.
  - `commands_stream_key`     : canonical Redis Stream key builder.
"""

from verbio_engine.commands.bus import (
    CommandBus,
    NullCommandBus,
    RedisCommandStreamBus,
    commands_stream_key,
)

__all__ = [
    "CommandBus",
    "NullCommandBus",
    "RedisCommandStreamBus",
    "commands_stream_key",
]
