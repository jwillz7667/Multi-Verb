/**
 * AUTO-GENERATED — DO NOT EDIT BY HAND.
 *
 * Source: Pydantic models in services/engine/verbio_engine/domain/.
 * Regenerate via: pnpm shared-types:generate
 */

/* eslint-disable */

/**
 * Stable identifier for the rule-set release this session uses (e.g. 'v1.0'). The registry refuses sessions whose version it does not know about.
 */
export type RulesVersion = string;

/**
 * Frozen bundle of rule configurations for one session.
 *
 * Stored verbatim inside `sessions.config_snapshot` (alongside the
 * persona, retention policy, etc.) so a replay can reconstitute the
 * rule set without re-reading the study row, which may have been
 * edited since.
 */
export interface RulesConfig {
  rules?: Rules;
  rules_version: RulesVersion;
}
/**
 * Per-rule overrides keyed by `rule.name`. Each value is a plain dict that the corresponding rule class parses into its own typed config. Missing entries mean 'use rule defaults'.
 */
export interface Rules {
  [k: string]:
    | {
        [k: string]: unknown | undefined;
      }
    | undefined;
}
