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

from verbio_engine.commands.budget import (
    BUDGET_COMMAND_TYPES,
    BudgetControl,
    BudgetEffects,
    apply_budget_commands,
)
from verbio_engine.commands.bus import (
    CommandBus,
    NullCommandBus,
    RedisCommandStreamBus,
    commands_stream_key,
)
from verbio_engine.commands.control import (
    CONTROL_COMMAND_TYPES,
    ControlEffects,
    RuntimeControl,
    apply_control_commands,
)
from verbio_engine.commands.translator import (
    MANUAL_COOLDOWN_SEC,
    OVERRIDING_COMMAND_TYPES,
    SPOKEN_COMMAND_TYPES,
    WHISPER_COMMAND_TYPE,
    build_manual_decision,
    build_whisper_decision,
    first_overriding_command,
    first_spoken_command,
)

__all__ = [
    "BUDGET_COMMAND_TYPES",
    "CONTROL_COMMAND_TYPES",
    "MANUAL_COOLDOWN_SEC",
    "OVERRIDING_COMMAND_TYPES",
    "SPOKEN_COMMAND_TYPES",
    "WHISPER_COMMAND_TYPE",
    "BudgetControl",
    "BudgetEffects",
    "CommandBus",
    "ControlEffects",
    "NullCommandBus",
    "RedisCommandStreamBus",
    "RuntimeControl",
    "apply_budget_commands",
    "apply_control_commands",
    "build_manual_decision",
    "build_whisper_decision",
    "commands_stream_key",
    "first_overriding_command",
    "first_spoken_command",
]
