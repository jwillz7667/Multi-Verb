"""`ResearcherCommand` → `ModeratorDecision` translator (P5 L3).

A `force_prompt` / `force_redirect` / `force_summary` command overrides
the rules engine for one tick — the moderator speaks the researcher's
intent regardless of cooldowns or the quietness budget. The translator
is the pure mapping from command wire shape to `ModeratorDecision`; the
listener handles ordering (drain → resolve → maybe-override → persist).

Why "spoken" commands and not all of them? The other command variants
have non-decision semantics: `whisper` bypasses the mouth and goes
straight to TTS (own path, P5 L4); `mute_moderator`, `pause_session`,
and friends are control-plane (P5 L5); `set_quietness_budget` mutates
state (P5 L6); `flag_moment` is a bookmark with no audio (P5 L7). Only
the three force_* variants here translate to a `ModeratorDecision`
that the executor can speak.

Decision-id contract: the listener reuses the resolver's tick decision
id as the manual decision's id, so the rules' per-tick `rule_evaluations`
stay FK-linked to the row researchers actually see. The translator
takes the id as input rather than minting its own — keeps the listener
in charge of id allocation.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final, get_args

from verbio_engine.domain.command import (
    ForcePromptPayload,
    ForceRedirectPayload,
    ForceSummaryPayload,
    ResearcherCommandType,
)
from verbio_engine.domain.decision import DecisionAction, ModeratorDecision

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime
    from uuid import UUID

    from verbio_engine.domain.command import ResearcherCommand
    from verbio_engine.domain.session_state import SessionState


SPOKEN_COMMAND_TYPES: Final[frozenset[ResearcherCommandType]] = frozenset(
    {"force_prompt", "force_redirect", "force_summary"},
)
"""Command types that translate to a ModeratorDecision the executor speaks.

`whisper` is intentionally NOT in here — it bypasses the mouth layer
entirely (P5 L4) and reuses the executor under a different code path.
"""

# Compile-time guard: every member of SPOKEN_COMMAND_TYPES must be a
# valid `ResearcherCommandType`. If a future commit narrows the Literal,
# this assertion fails at import — far better than a silent KeyError at
# the first runtime override.
_VALID_COMMAND_TYPES: Final[frozenset[str]] = frozenset(get_args(ResearcherCommandType))
assert (
    SPOKEN_COMMAND_TYPES <= _VALID_COMMAND_TYPES
), "SPOKEN_COMMAND_TYPES drifted from ResearcherCommandType"


# Per-spoken-command cooldown after a researcher manual override. The
# brief doesn't pin a value here (§7.4 cooldowns are per-rule), but
# zero would let rapid double-clicks fire two utterances back-to-back —
# uncomfortable for participants and indistinguishable from a UI bug.
# Deliberately well under `QuietnessBudget.min_seconds_between_utterances`
# (default 30s): the whole point of a researcher override is to bypass
# the budget when the moment calls for it. 3s is enough to debounce
# accidental double-presses without throttling a researcher who genuinely
# wants to issue several commands in succession.
MANUAL_COOLDOWN_SEC: Final[float] = 3.0


def first_spoken_command(commands: Iterable[ResearcherCommand]) -> ResearcherCommand | None:
    """Return the first force_prompt/redirect/summary in iteration order.

    FIFO matches the Redis Stream's `XADD` ordering — researchers who
    fire two spoken commands in the same tick see the earlier one win
    (and the later one logged in researcher_actions with no resulting
    decision). The brief calls this out implicitly: §11.3 says the bus
    is ordered, and researcher overrides aren't multi-merged.
    """
    for cmd in commands:
        if cmd.command_type in SPOKEN_COMMAND_TYPES:
            return cmd
    return None


def build_manual_decision(
    *,
    command: ResearcherCommand,
    state: SessionState,
    t: datetime,
    decision_id: UUID,
) -> ModeratorDecision:
    """Materialise a `ModeratorDecision` for one spoken researcher command.

    The decision bypasses cooldowns and the quietness budget — the
    researcher's call is final for this tick. `confidence=1.0` reflects
    "human in the loop chose this"; `suppressed_by=[]` since nothing
    blocked us. `reason_codes` carries the originating command type so
    the dashboard can render "Researcher override · force_prompt".
    """
    target: str | None
    hint: str | None
    action: DecisionAction
    if command.command_type == "force_prompt":
        prompt_payload = ForcePromptPayload.model_validate(command.payload)
        target = (
            str(prompt_payload.target_participant_id)
            if prompt_payload.target_participant_id is not None
            else None
        )
        hint = prompt_payload.prompt
        action = "prompt_participant"
    elif command.command_type == "force_redirect":
        redirect_payload = ForceRedirectPayload.model_validate(command.payload)
        target = None
        hint = redirect_payload.topic
        action = "redirect_topic"
    elif command.command_type == "force_summary":
        summary_payload = ForceSummaryPayload.model_validate(command.payload)
        target = None
        hint = summary_payload.focus  # may be None — summarise the full thread
        action = "summarize_thread"
    else:
        # `first_spoken_command` guards this — but a defensive check
        # makes the function safe to call directly from a future caller.
        msg = (
            f"command_type {command.command_type!r} does not translate to a ModeratorDecision; "
            f"only {sorted(SPOKEN_COMMAND_TYPES)} are spoken commands."
        )
        raise ValueError(msg)

    return ModeratorDecision(
        decision_id=decision_id,
        session_id=state.session_id,
        tick_id=state.tick_id,
        timestamp=t,
        action=action,
        target_participant_id=target,
        source="researcher_manual",
        triggering_rule=None,
        researcher_id=command.researcher_id,
        researcher_hint=hint,
        reason_codes=[f"researcher_command:{command.command_type}"],
        reason_human="",
        confidence=1.0,
        suppressed_by=[],
        was_executed=False,
        llm_prompt=None,
        llm_output=None,
        tts_audio_url=None,
        spoken_at=None,
        cooldown_until=t + timedelta(seconds=MANUAL_COOLDOWN_SEC),
    )
