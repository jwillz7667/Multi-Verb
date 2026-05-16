"""LiveKit moderator agent — Phase 1 audio plumbing.

Public surface:
  - `SessionRuntime`       : transport-agnostic per-session orchestrator
  - `ParticipantSnapshot`  : lightweight DTO the LiveKit glue passes in
  - `run_worker`           : LiveKit Agents CLI entrypoint

`SessionRuntime` is the heart of the agent. It owns the per-session
persistence transitions (session start/end, participant join/leave) and
is intentionally independent of the LiveKit SDK so it can be unit-tested
without spinning up a real room. The LiveKit-facing glue lives in
`agent.worker` and calls `SessionRuntime` from event callbacks.
"""

from verbio_engine.agent.runtime import ParticipantSnapshot, SessionRuntime
from verbio_engine.agent.worker import run_worker

__all__ = [
    "ParticipantSnapshot",
    "SessionRuntime",
    "run_worker",
]
