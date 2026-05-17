"""`PhrasingContext` + `extract_phrasing_context` — the §8.1 narrow seam.

The mouth layer must not see `SessionState` or `ParticipantState`. This
test module pins both halves of that contract:

  * `PhrasingContext` validates the wire shape (frozen, strict, bounded).
  * `extract_phrasing_context` projects a real `SessionState` down to
    the four documented fields and clamps the edge cases the LLM would
    otherwise see (clock skew, missing target, never-spoken speaker).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from tests.rules.fixtures import NOW, make_participant, make_session_state
from verbio_engine.domain import ParticipantFlags, UtteranceRef
from verbio_engine.mouth.context import PhrasingContext, extract_phrasing_context


class TestPhrasingContextValidation:
    def test_all_fields_default_to_none(self) -> None:
        # An untargeted action with no recent speaker still produces a
        # valid context — `build_prompt` interprets the Nones.
        ctx = PhrasingContext()
        assert ctx.target_display_name is None
        assert ctx.target_last_contribution_minutes_ago is None
        assert ctx.target_engagement_note is None
        assert ctx.last_speaker_quote is None

    def test_is_frozen(self) -> None:
        ctx = PhrasingContext()
        with pytest.raises(ValidationError):
            ctx.target_display_name = "Alice"  # type: ignore[misc]

    def test_rejects_extra_fields(self) -> None:
        # If we ever want to add a field, the addition is reviewed
        # centrally — not smuggled in by a caller bypassing the seam.
        with pytest.raises(ValidationError, match="Extra inputs"):
            PhrasingContext(extra_field="state-leak")  # type: ignore[call-arg]

    def test_rejects_negative_minutes_ago(self) -> None:
        with pytest.raises(ValidationError):
            PhrasingContext(target_last_contribution_minutes_ago=-1.0)

    def test_rejects_overlong_engagement_note(self) -> None:
        with pytest.raises(ValidationError):
            PhrasingContext(target_engagement_note="x" * 81)

    def test_rejects_overlong_last_quote(self) -> None:
        with pytest.raises(ValidationError):
            PhrasingContext(last_speaker_quote="x" * 241)


class TestExtractPhrasingContextUntargeted:
    def test_returns_empty_when_no_target_and_no_speakers(self) -> None:
        # redirect_topic / summarize_thread / suggest_turn_taking with
        # silent room — everything resolves to None.
        state = make_session_state(participants={})
        ctx = extract_phrasing_context(state, target_participant_id=None, now=NOW)
        assert ctx == PhrasingContext()

    def test_includes_last_speaker_quote_when_no_target(self) -> None:
        # redirect_topic against a noisy room — we still want the quote
        # so the LLM can reference what just happened.
        utt = UtteranceRef(
            utterance_id="u-1",
            text="Let's pivot to pricing then.",
            spoken_at=NOW - timedelta(seconds=10),
            duration_sec=2.0,
        )
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    recent_utterances=[utt],
                ),
            },
        )
        ctx = extract_phrasing_context(state, target_participant_id=None, now=NOW)
        assert ctx.target_display_name is None
        assert ctx.last_speaker_quote == "Let's pivot to pricing then."


class TestExtractPhrasingContextTargeted:
    def test_targeted_includes_name_and_minutes_since_last_spoke(self) -> None:
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    display_name="Alice",
                    last_spoke_at=NOW - timedelta(minutes=4, seconds=30),
                ),
            },
        )
        ctx = extract_phrasing_context(state, target_participant_id="p-1", now=NOW)
        assert ctx.target_display_name == "Alice"
        assert ctx.target_last_contribution_minutes_ago == pytest.approx(4.5)

    def test_targeted_never_spoken_omits_minutes(self) -> None:
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    display_name="Alice",
                    last_spoke_at=None,
                ),
            },
        )
        ctx = extract_phrasing_context(state, target_participant_id="p-1", now=NOW)
        assert ctx.target_display_name == "Alice"
        assert ctx.target_last_contribution_minutes_ago is None

    def test_clamps_negative_minutes_for_clock_skew(self) -> None:
        # NTP / monotonic-clock drift can push `last_spoke_at` a few
        # millis past `now`. Without the clamp, Pydantic's
        # NonNegativeFloat would explode at the seam.
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW + timedelta(milliseconds=2),
                ),
            },
        )
        ctx = extract_phrasing_context(state, target_participant_id="p-1", now=NOW)
        assert ctx.target_last_contribution_minutes_ago == 0.0

    def test_missing_target_id_yields_no_target_fields(self) -> None:
        # Defensive: a stale `target_participant_id` from a prior tick
        # (participant left the room) must degrade quietly, not crash.
        state = make_session_state(
            participants={"p-1": make_participant(participant_id="p-1")},
        )
        ctx = extract_phrasing_context(
            state,
            target_participant_id="p-ghost",
            now=NOW,
        )
        assert ctx.target_display_name is None
        assert ctx.target_last_contribution_minutes_ago is None
        assert ctx.target_engagement_note is None


class TestExtractPhrasingContextEngagementNote:
    def test_backchannel_dominates(self) -> None:
        # Priority order matches the source: backchannels first, then
        # disengaged flag, then was-interrupted. If a participant is
        # actively backchanneling, we lead with that — it's the most
        # actionable signal for a prompt_participant intervention.
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    backchannel_count_last_2min=3,
                    flags=ParticipantFlags(disengaged=True),
                    was_interrupted_count=2,
                ),
            },
        )
        ctx = extract_phrasing_context(state, target_participant_id="p-1", now=NOW)
        assert ctx.target_engagement_note == "has been actively listening"

    def test_disengaged_when_no_backchannel(self) -> None:
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    flags=ParticipantFlags(disengaged=True),
                ),
            },
        )
        ctx = extract_phrasing_context(state, target_participant_id="p-1", now=NOW)
        assert ctx.target_engagement_note == "appears disengaged"

    def test_was_interrupted_when_no_backchannel_or_disengaged(self) -> None:
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    was_interrupted_count=2,
                ),
            },
        )
        ctx = extract_phrasing_context(state, target_participant_id="p-1", now=NOW)
        assert ctx.target_engagement_note == "was interrupted earlier"

    def test_no_note_when_signals_neutral(self) -> None:
        state = make_session_state(
            participants={"p-1": make_participant(participant_id="p-1")},
        )
        ctx = extract_phrasing_context(state, target_participant_id="p-1", now=NOW)
        assert ctx.target_engagement_note is None


class TestExtractPhrasingContextLastSpeakerQuote:
    def test_picks_most_recent_speaker_when_multiple(self) -> None:
        older = UtteranceRef(
            utterance_id="u-older",
            text="That was earlier.",
            spoken_at=NOW - timedelta(minutes=2),
            duration_sec=1.0,
        )
        newer = UtteranceRef(
            utterance_id="u-newer",
            text="And this is now.",
            spoken_at=NOW - timedelta(seconds=5),
            duration_sec=1.5,
        )
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(minutes=2),
                    recent_utterances=[older],
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(seconds=5),
                    recent_utterances=[newer],
                ),
            },
        )
        ctx = extract_phrasing_context(state, target_participant_id=None, now=NOW)
        assert ctx.last_speaker_quote == "And this is now."

    def test_picks_last_utterance_when_speaker_has_multiple(self) -> None:
        first = UtteranceRef(
            utterance_id="u-1",
            text="First thought.",
            spoken_at=NOW - timedelta(seconds=30),
            duration_sec=1.0,
        )
        last = UtteranceRef(
            utterance_id="u-2",
            text="Then this one.",
            spoken_at=NOW - timedelta(seconds=10),
            duration_sec=1.0,
        )
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    recent_utterances=[first, last],
                ),
            },
        )
        ctx = extract_phrasing_context(state, target_participant_id=None, now=NOW)
        assert ctx.last_speaker_quote == "Then this one."

    def test_trims_quote_to_240_chars(self) -> None:
        long_text = "abcdefghij" * 30  # 300 chars
        utt = UtteranceRef(
            utterance_id="u-1",
            text=long_text,
            spoken_at=NOW - timedelta(seconds=5),
            duration_sec=8.0,
        )
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=5),
                    recent_utterances=[utt],
                ),
            },
        )
        ctx = extract_phrasing_context(state, target_participant_id=None, now=NOW)
        assert ctx.last_speaker_quote is not None
        assert len(ctx.last_speaker_quote) == 240
        assert ctx.last_speaker_quote == long_text[:240]

    def test_no_quote_when_speaker_has_no_recent_utterances(self) -> None:
        # `last_spoke_at` is set (somebody spoke), but the utterance ref
        # has rolled out of the 5-slot rolling buffer.
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=20),
                    recent_utterances=[],
                ),
            },
        )
        ctx = extract_phrasing_context(state, target_participant_id=None, now=NOW)
        assert ctx.last_speaker_quote is None
