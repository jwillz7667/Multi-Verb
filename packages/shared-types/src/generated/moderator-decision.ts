/**
 * AUTO-GENERATED — DO NOT EDIT BY HAND.
 *
 * Source: Pydantic models in services/engine/verbio_engine/domain/.
 * Regenerate via: pnpm shared-types:generate
 */

/* eslint-disable */

export type Action =
  | 'stay_silent'
  | 'prompt_participant'
  | 'redirect_topic'
  | 'summarize_thread'
  | 'request_clarification'
  | 'suggest_turn_taking'
  | 'close_session';
export type Confidence = number;
export type CooldownUntil = string;
export type DecisionId = string;
export type LlmOutput = string | null;
/**
 * Verbatim mouth-layer input; tightened to a typed shape in Phase 4.
 */
export type LlmPrompt = {
  [k: string]: unknown | undefined;
} | null;
/**
 * Structured codes, e.g. ['silence_gap_8s', 'p3_unheard_4min'].
 */
export type ReasonCodes = string[];
/**
 * Generated single-sentence rationale shown on the dashboard.
 */
export type ReasonHuman = string;
/**
 * Free-text guidance from a manual researcher command.
 */
export type ResearcherHint = string | null;
export type ResearcherId = string | null;
export type SessionId = string;
export type Source = 'auto' | 'researcher_manual' | 'researcher_whisper';
export type SpokenAt = string | null;
/**
 * ['quietness_budget', 'global_cooldown', 'lower_priority_won', ...].
 */
export type SuppressedBy = string[];
export type TargetParticipantId = string | null;
/**
 * Monotonic per-session tick counter.
 */
export type TickId = number;
export type Timestamp = string;
/**
 * Rule name when source='auto'; null otherwise.
 */
export type TriggeringRule = string | null;
export type TtsAudioUrl = string | null;
export type WasExecuted = boolean;

/**
 * Single decision record from one tick of the engine.
 *
 * Invariants enforced elsewhere (`verbio_engine.tick_loop`):
 *   * `was_executed` is True only for non-silent actions that completed
 *     before the latency budget elapsed.
 *   * `cooldown_until` is computed from the triggering rule's
 *     `default_cooldown_sec`; researcher overrides may extend it.
 */
export interface ModeratorDecision {
  action: Action;
  confidence?: Confidence;
  cooldown_until: CooldownUntil;
  decision_id: DecisionId;
  llm_output?: LlmOutput;
  llm_prompt?: LlmPrompt;
  reason_codes?: ReasonCodes;
  reason_human?: ReasonHuman;
  researcher_hint?: ResearcherHint;
  researcher_id?: ResearcherId;
  session_id: SessionId;
  source: Source;
  spoken_at?: SpokenAt;
  suppressed_by?: SuppressedBy;
  target_participant_id?: TargetParticipantId;
  tick_id: TickId;
  timestamp: Timestamp;
  triggering_rule?: TriggeringRule;
  tts_audio_url?: TtsAudioUrl;
  was_executed?: WasExecuted;
}
