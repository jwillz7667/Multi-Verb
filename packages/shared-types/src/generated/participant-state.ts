/**
 * AUTO-GENERATED — DO NOT EDIT BY HAND.
 *
 * Source: Pydantic models in services/engine/verbio_engine/domain/.
 * Regenerate via: pnpm shared-types:generate
 */

/* eslint-disable */

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
