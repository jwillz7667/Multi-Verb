/**
 * AUTO-GENERATED — DO NOT EDIT BY HAND.
 *
 * Source: Pydantic models in services/engine/verbio_engine/domain/.
 * Regenerate via: pnpm shared-types:generate
 */

/* eslint-disable */

export type TranscriptEvent = UtteranceEventEnvelope | StateSnapshotEventEnvelope;
export type Id = string;
export type Confidence = number | null;
export type EndTs = string;
export type IsFinal = boolean;
export type ParticipantDisplayName = string;
export type ParticipantId = string;
export type ParticipantIdentity = string;
export type SessionId = string;
export type StartTs = string;
export type Text = string;
export type UtteranceId = string;
export type SessionId1 = string;
export type Ts = string;
export type Type = 'utterance';
export type Id1 = string;
export type SnapshotId = string;
/**
 * Number of participants whose VAD or STT signals speech right now.
 */
export type CurrentlySpeakingCount = number;
/**
 * (t - started_at) in seconds; precomputed for convenience.
 */
export type ElapsedSec = number;
/**
 * Set by `pause_session`; the tick loop still runs but the moderator is suppressed from speaking.
 */
export type IsPaused = boolean;
/**
 * Set by `mute_moderator`; rules still evaluate so the audit trail remains complete.
 */
export type ModeratorMuted = boolean;
export type ActualShareLast5MinPct = number;
export type BackchannelCountLast2Min = number;
/**
 * Human-readable label shown to researchers.
 */
export type DisplayName = string;
export type FairSharePct = number;
/**
 * No backchannels and no speech in the recent window.
 */
export type Disengaged = boolean;
/**
 * Actual share of last 5 min exceeds fair share by configured factor.
 */
export type Dominating = boolean;
/**
 * `was_interrupted_count` exceeds threshold over the rolling window.
 */
export type FrequentlyInterrupted = boolean;
/**
 * Has not spoken in N minutes (configured per study).
 */
export type SilentTooLong = boolean;
/**
 * Times this participant cut someone else off.
 */
export type InterruptionCount = number;
export type IsCurrentlySpeaking = boolean;
export type JoinedAt = string;
export type LastSpokeAt = string | null;
export type LastSpokeDurationSec = number | null;
/**
 * Stable application id.
 */
export type ParticipantId1 = string;
/**
 * @maxItems 5
 */
export type RecentUtterances =
  | []
  | [UtteranceRef]
  | [UtteranceRef, UtteranceRef]
  | [UtteranceRef, UtteranceRef, UtteranceRef]
  | [UtteranceRef, UtteranceRef, UtteranceRef, UtteranceRef]
  | [UtteranceRef, UtteranceRef, UtteranceRef, UtteranceRef, UtteranceRef];
/**
 * Length of the utterance in seconds.
 */
export type DurationSec = number;
/**
 * Wall-clock start time of the utterance.
 */
export type SpokenAt = string;
/**
 * Verbatim transcribed text (may be partial).
 */
export type Text1 = string;
/**
 * Stable id; references utterances.utterance_id.
 */
export type UtteranceId1 = string;
export type RollingTranscript2Min = string;
export type SpeakingTimeLast5MinSec = number;
export type SpeakingTimeLast60Sec = number;
export type SpeakingTimeTotalSec = number;
export type TurnCount = number;
/**
 * Voice activity detected but not yet transcribed.
 */
export type VadActive = boolean;
export type WasInterruptedCount = number;
/**
 * Utterances spoken inside the active rolling window.
 */
export type CurrentWindowCount = number;
export type LastUtteranceAt = string | null;
/**
 * Hard cap on moderator utterances within the rolling 10-minute window.
 */
export type MaxUtterancesPer10Min = number;
/**
 * Floor enforced regardless of remaining window capacity.
 */
export type MinSecondsBetweenUtterances = number;
/**
 * Concatenated finalized text from all speakers in the last 2 min, newest last. Used by `topic_drift` and `stalled_thread`.
 */
export type RollingGlobalTranscript2Min = string;
/**
 * Planned end time; powers `time_remaining_pressure` rule.
 */
export type ScheduledEndAt = string | null;
export type SessionId2 = string;
/**
 * Seconds since the most recent global speech event; `silence_gap` rule reads this directly.
 */
export type SilenceRunSec = number;
/**
 * Wall-clock when the session opened.
 */
export type StartedAt = string;
/**
 * Wall-clock for the current tick.
 */
export type T = string;
/**
 * Monotonic per-session tick counter. First tick is 0.
 */
export type TickId = number;
export type SessionId3 = string;
export type Ts1 = string;
export type Type1 = 'state_snapshot';

/**
 * SSE envelope for an STT-derived utterance.
 *
 * `id` is the utterance UUID as a string — SSE clients echo it back as
 * `Last-Event-ID` on reconnect, and the web SSE route uses it to skip
 * already-seen rows during the Postgres backfill.
 *
 * `ts` is the server-side moment the event was created (not the
 * utterance's start_ts), used purely for diagnostics — ordering is by
 * payload start_ts on backfill.
 */
export interface UtteranceEventEnvelope {
  id: Id;
  payload: UtteranceEventPayload;
  session_id: SessionId1;
  ts: Ts;
  type: Type;
}
/**
 * Snapshot of an utterance row at the moment it was persisted.
 *
 * Self-contained — the dashboard renders without joining back to
 * Postgres. `participant_identity` + `participant_display_name` are
 * denormalised so a single SSE message is enough to draw a row.
 */
export interface UtteranceEventPayload {
  confidence?: Confidence;
  end_ts: EndTs;
  is_final: IsFinal;
  participant_display_name: ParticipantDisplayName;
  participant_id: ParticipantId;
  participant_identity: ParticipantIdentity;
  session_id: SessionId;
  start_ts: StartTs;
  text: Text;
  utterance_id: UtteranceId;
}
/**
 * SSE envelope for one per-tick SessionState snapshot.
 *
 * `id` is the snapshot row UUID as a string; `ts` is the wall-clock
 * moment the tick projected the snapshot (mirrors `state.t`). The
 * Last-Event-ID cursor uses `id` to backfill from
 * `state_snapshots.id > {last_id}` on reconnect — the table's
 * (session_id, tick_id) index covers that scan cheaply.
 */
export interface StateSnapshotEventEnvelope {
  id: Id1;
  payload: StateSnapshotEventPayload;
  session_id: SessionId3;
  ts: Ts1;
  type: Type1;
}
/**
 * Snapshot of one full `SessionState` row at the moment it was persisted.
 *
 * Carries the entire SessionState so the dashboard can render every
 * tile (speaking time, last spoke, currently_speaking, silence_run,
 * flags) from a single message. `snapshot_id` is the Postgres row id;
 * keeping it here as well as on the envelope mirrors the utterance
 * variant and gives the web side typed-UUID access without re-parsing
 * the envelope id.
 */
export interface StateSnapshotEventPayload {
  snapshot_id: SnapshotId;
  state: SessionState;
}
/**
 * Per-tick projection of everything a rule needs to read.
 */
export interface SessionState {
  currently_speaking_count?: CurrentlySpeakingCount;
  elapsed_sec: ElapsedSec;
  is_paused?: IsPaused;
  moderator_muted?: ModeratorMuted;
  participants?: Participants;
  quietness_budget?: QuietnessBudget;
  rolling_global_transcript_2min?: RollingGlobalTranscript2Min;
  scheduled_end_at?: ScheduledEndAt;
  session_id: SessionId2;
  silence_run_sec?: SilenceRunSec;
  started_at: StartedAt;
  t: T;
  tick_id: TickId;
}
/**
 * Keyed by `participant_id`. Only currently-joined participants.
 */
export interface Participants {
  [k: string]: ParticipantState | undefined;
}
/**
 * Per-participant snapshot consumed by every rule on every tick.
 */
export interface ParticipantState {
  actual_share_last_5min_pct?: ActualShareLast5MinPct;
  backchannel_count_last_2min?: BackchannelCountLast2Min;
  display_name: DisplayName;
  fair_share_pct?: FairSharePct;
  flags?: ParticipantFlags;
  interruption_count?: InterruptionCount;
  is_currently_speaking?: IsCurrentlySpeaking;
  joined_at: JoinedAt;
  last_spoke_at?: LastSpokeAt;
  last_spoke_duration_sec?: LastSpokeDurationSec;
  participant_id: ParticipantId1;
  recent_utterances?: RecentUtterances;
  rolling_transcript_2min?: RollingTranscript2Min;
  speaking_time_last_5min_sec?: SpeakingTimeLast5MinSec;
  speaking_time_last_60sec?: SpeakingTimeLast60Sec;
  speaking_time_total_sec?: SpeakingTimeTotalSec;
  turn_count?: TurnCount;
  vad_active?: VadActive;
  was_interrupted_count?: WasInterruptedCount;
}
/**
 * Derived booleans recomputed each tick from rolling state.
 *
 * Predicates are documented in `docs/rules-reference.md`. Each rule's
 * predicate may consume one or more of these flags.
 */
export interface ParticipantFlags {
  disengaged?: Disengaged;
  dominating?: Dominating;
  frequently_interrupted?: FrequentlyInterrupted;
  silent_too_long?: SilentTooLong;
}
/**
 * A pointer to a recent utterance with the timing metadata the rules need.
 */
export interface UtteranceRef {
  duration_sec: DurationSec;
  spoken_at: SpokenAt;
  text: Text1;
  utterance_id: UtteranceId1;
}
/**
 * Live-adjustable rate limit (brief §7.4).
 */
export interface QuietnessBudget {
  current_window_count?: CurrentWindowCount;
  last_utterance_at?: LastUtteranceAt;
  max_utterances_per_10min?: MaxUtterancesPer10Min;
  min_seconds_between_utterances?: MinSecondsBetweenUtterances;
}
