/**
 * AUTO-GENERATED — DO NOT EDIT BY HAND.
 *
 * Source: Pydantic models in services/engine/verbio_engine/domain/.
 * Regenerate via: pnpm shared-types:generate
 */

/* eslint-disable */

/**
 * Number of participants whose VAD or STT signals speech right now.
 */
export type CurrentlySpeakingCount = number;
/**
 * (t - started_at) in seconds; precomputed for convenience.
 */
export type ElapsedSec = number;
/**
 * Model identifier that produced both embeddings on this snapshot. Persisted into state snapshots so replay can refuse a cross-model comparison.
 */
export type EmbeddingModelName = string | null;
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
export type ParticipantId = string;
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
export type Text = string;
/**
 * Stable id; references utterances.utterance_id.
 */
export type UtteranceId = string;
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
 * Vector of the last ~30s of group transcript. Refreshed by the state store on each speech-finalization event; lags the raw transcript field by one embed round-trip. None until enough transcript has accumulated to embed.
 */
export type RollingTranscript30SEmbedding = number[] | null;
/**
 * Planned end time; powers `time_remaining_pressure` rule.
 */
export type ScheduledEndAt = string | null;
export type SessionId = string;
/**
 * Seconds since the most recent global speech event; `silence_gap` rule reads this directly.
 */
export type SilenceRunSec = number;
/**
 * Wall-clock when the session opened.
 */
export type StartedAt = string;
/**
 * The researcher's framing prompt for this study; embedded once at session load and reused for similarity comparisons.
 */
export type StudyPrompt = string;
/**
 * Vector representation of `study_prompt`. None until the state store has run the embedding call; rules MUST handle the None case (typically: don't fire) so a transient provider outage doesn't fabricate false positives.
 */
export type StudyPromptEmbedding = number[] | null;
/**
 * Wall-clock for the current tick.
 */
export type T = string;
/**
 * Monotonic per-session tick counter. First tick is 0.
 */
export type TickId = number;

/**
 * Per-tick projection of everything a rule needs to read.
 */
export interface SessionState {
  currently_speaking_count?: CurrentlySpeakingCount;
  elapsed_sec: ElapsedSec;
  embedding_model_name?: EmbeddingModelName;
  is_paused?: IsPaused;
  moderator_muted?: ModeratorMuted;
  participants?: Participants;
  quietness_budget?: QuietnessBudget;
  rolling_global_transcript_2min?: RollingGlobalTranscript2Min;
  rolling_transcript_30s_embedding?: RollingTranscript30SEmbedding;
  scheduled_end_at?: ScheduledEndAt;
  session_id: SessionId;
  silence_run_sec?: SilenceRunSec;
  started_at: StartedAt;
  study_prompt?: StudyPrompt;
  study_prompt_embedding?: StudyPromptEmbedding;
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
  participant_id: ParticipantId;
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
  text: Text;
  utterance_id: UtteranceId;
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
