/**
 * AUTO-GENERATED — DO NOT EDIT BY HAND.
 *
 * Source: Pydantic models in services/engine/verbio_engine/domain/.
 * Regenerate via: pnpm shared-types:generate
 */

/* eslint-disable */

export type Confidence = number;
/**
 * The decision this evaluation belongs to.
 */
export type DecisionId = string;
export type EvaluationId = string;
export type Fired = boolean;
export type RuleName = string;
/**
 * Snapshotted at session start; replay must use this version.
 */
export type RuleVersion = string;
/**
 * 'cooldown' | 'lower_priority_won' | 'disabled' | 'quietness_budget'.
 */
export type SuppressedReason = string | null;

/**
 * One rule's evaluation result on one tick.
 */
export interface RuleEvaluation {
  confidence?: Confidence;
  decision_id: DecisionId;
  evaluation_id: EvaluationId;
  fired: Fired;
  predicate_inputs?: PredicateInputs;
  rule_name: RuleName;
  rule_version: RuleVersion;
  suppressed_reason?: SuppressedReason;
}
/**
 * Snapshot of the values the predicate read for this tick.
 */
export interface PredicateInputs {
  [k: string]: unknown | undefined;
}
