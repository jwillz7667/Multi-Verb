"""`ResearcherCommand` → `ModeratorDecision` translator (P5 L3 + L4).

A `force_prompt` / `force_redirect` / `force_summary` / `whisper`
command overrides the rules engine for one tick — the moderator speaks
the researcher's intent regardless of cooldowns or the quietness budget.
The translator is the pure mapping from command wire shape to
`ModeratorDecision`; the listener handles ordering (drain → resolve →
maybe-override → persist).

Two override flavours share the same dispatch slot:

  * Force commands (`force_prompt`, `force_redirect`, `force_summary`)
    produce a `ModeratorDecision` with `source="researcher_manual"`.
    The mouth layer phrases the actual utterance from `researcher_hint`.

  * Whisper (`whisper`) produces a `ModeratorDecision` with
    `source="researcher_whisper"`. The executor reads the verbatim
    `researcher_hint` text straight into TTS — no mouth call, no
    rephrasing. This is the "speak exactly these words" path.

Why not all command variants? The remaining types have non-decision
semantics handled in later layers: `mute_moderator`, `pause_session`,
and friends are control-plane (P5 L5); `set_quietness_budget` mutates
state (P5 L6); `flag_moment` is a bookmark with no audio (P5 L7).

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
    WhisperPayload,
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
"""Force commands — translate to a ModeratorDecision the *mouth* phrases.

`whisper` is intentionally NOT in here — it overrides the resolver too,
but bypasses the mouth layer and uses the executor under a different
code path. See `WHISPER_COMMAND_TYPE` / `OVERRIDING_COMMAND_TYPES`.
"""

WHISPER_COMMAND_TYPE: Final[ResearcherCommandType] = "whisper"
"""The single command type that produces a verbatim-text decision (P5 L4).

A whisper carries the exact words the researcher wants spoken; the
executor sends them straight to TTS without the mouth's rephrasing pass.
"""

OVERRIDING_COMMAND_TYPES: Final[frozenset[ResearcherCommandType]] = SPOKEN_COMMAND_TYPES | {
    WHISPER_COMMAND_TYPE
}
"""All command types that override the resolver's decision for one tick.

The listener picks at most one override per tick (FIFO from the drained
batch) so the moderator never collides two researcher-issued utterances.
"""

# Compile-time guard: every member of the override sets must be a
# valid `ResearcherCommandType`. If a future commit narrows the Literal,
# this assertion fails at import — far better than a silent KeyError at
# the first runtime override.
_VALID_COMMAND_TYPES: Final[frozenset[str]] = frozenset(get_args(ResearcherCommandType))
assert (
    OVERRIDING_COMMAND_TYPES <= _VALID_COMMAND_TYPES
), "OVERRIDING_COMMAND_TYPES drifted from ResearcherCommandType"


# Per-override cooldown after a researcher command. The brief doesn't pin
# a value here (§7.4 cooldowns are per-rule), but zero would let rapid
# double-clicks fire two utterances back-to-back — uncomfortable for
# participants and indistinguishable from a UI bug. Deliberately well
# under `QuietnessBudget.min_seconds_between_utterances` (default 30s):
# the whole point of a researcher override is to bypass the budget when
# the moment calls for it. 3s is enough to debounce accidental double-
# presses without throttling a researcher who genuinely wants to issue
# several commands in succession.
MANUAL_COOLDOWN_SEC: Final[float] = 3.0


def first_spoken_command(commands: Iterable[ResearcherCommand]) -> ResearcherCommand | None:
    """Return the first force_prompt/redirect/summary in iteration order.

    Kept as a narrower companion to `first_overriding_command` for tests
    and call sites that only care about mouth-phrased overrides (e.g., a
    future analytics counter that splits "manual" from "whisper" rates).
    FIFO matches the Redis Stream's `XADD` ordering — researchers who
    fire two spoken commands in the same tick see the earlier one win.
    """
    for cmd in commands:
        if cmd.command_type in SPOKEN_COMMAND_TYPES:
            return cmd
    return None


def first_overriding_command(
    commands: Iterable[ResearcherCommand],
) -> ResearcherCommand | None:
    """Return the first force_* or whisper in iteration order.

    The listener uses this to pick the winning override per tick. FIFO
    follows the Redis Stream's `XADD` ordering (brief §11.3) — a whisper
    issued *after* a force_prompt in the same batch loses; the audit row
    still records both, but only the earlier one drives a decision.
    """
    for cmd in commands:
        if cmd.command_type in OVERRIDING_COMMAND_TYPES:
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


def build_whisper_decision(
    *,
    command: ResearcherCommand,
    state: SessionState,
    t: datetime,
    decision_id: UUID,
) -> ModeratorDecision:
    """Materialise a `ModeratorDecision` for one whisper command (P5 L4).

    Whisper carries the exact words the researcher wants spoken. The
    decision lands with `source="researcher_whisper"` so the executor
    knows to skip the mouth layer and send `researcher_hint` straight
    to TTS. Action is `prompt_participant` — a whisper *is* a prompt,
    just with the researcher's voice rather than the LLM's phrasing.

    Bypasses cooldowns and the quietness budget for the same reason
    force commands do: the researcher's call is final. The cooldown
    that lands on `cooldown_until` is the same 3 s debounce
    (`MANUAL_COOLDOWN_SEC`) that guards force commands — it exists to
    catch UI double-clicks, not to throttle deliberate sequences.
    """
    if command.command_type != WHISPER_COMMAND_TYPE:
        # Defensive — `first_overriding_command` + listener branching
        # guard this in practice, but a direct call should fail loudly.
        msg = (
            f"command_type {command.command_type!r} is not a whisper; "
            f"call build_manual_decision instead."
        )
        raise ValueError(msg)

    payload = WhisperPayload.model_validate(command.payload)
    target = (
        str(payload.target_participant_id) if payload.target_participant_id is not None else None
    )

    return ModeratorDecision(
        decision_id=decision_id,
        session_id=state.session_id,
        tick_id=state.tick_id,
        timestamp=t,
        action="prompt_participant",
        target_participant_id=target,
        source="researcher_whisper",
        triggering_rule=None,
        researcher_id=command.researcher_id,
        researcher_hint=payload.text,
        reason_codes=[f"researcher_command:{WHISPER_COMMAND_TYPE}"],
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
