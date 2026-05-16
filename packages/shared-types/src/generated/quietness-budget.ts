/**
 * AUTO-GENERATED — DO NOT EDIT BY HAND.
 *
 * Source: Pydantic models in services/engine/verbio_engine/domain/.
 * Regenerate via: pnpm shared-types:generate
 */

/* eslint-disable */

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
 * Per-session rate limit on moderator speech.
 */
export interface QuietnessBudget {
  current_window_count?: CurrentWindowCount;
  last_utterance_at?: LastUtteranceAt;
  max_utterances_per_10min?: MaxUtterancesPer10Min;
  min_seconds_between_utterances?: MinSecondsBetweenUtterances;
}
