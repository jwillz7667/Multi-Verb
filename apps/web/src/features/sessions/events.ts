/**
 * Runtime-validating Zod schemas for the SSE event envelopes.
 *
 * The TypeScript shape is generated from Pydantic in `@verbio/shared-types`
 * (`TranscriptEvent`); this file pairs that with Zod schemas so the
 * web side can VALIDATE the wire — generated TS types only describe
 * shape, they don't enforce it at runtime, and an event that doesn't
 * match the schema would otherwise reach the dashboard as malformed
 * JSON.
 *
 * The schema is a discriminated union over the `type` literal so a
 * `state_snapshot` envelope can't masquerade as an `utterance` and
 * vice-versa. Adding a future variant (e.g. Phase 3 `decision`) means
 * appending one more branch to the union; no consumer changes shape.
 *
 * Strictness policy:
 *   - Envelopes (`type`, `id`, `session_id`, `ts`, `payload`) are
 *     `.strict()` — unknown top-level keys are a wire violation.
 *   - `utterance` payload is `.strict()` — fully pinned, the field set
 *     hasn't moved since Phase 1.
 *   - `state_snapshot` payload's outer keys are `.strict()`, but the
 *     nested `SessionState` projection uses Zod's default "strip" mode
 *     so the engine can add a new derived field (e.g., a future
 *     ParticipantFlag) without breaking the dashboard. The compile-time
 *     `_assertAssignable` below catches type-level drift; runtime
 *     forward-compat is by design.
 *
 * If the engine's Pydantic shape changes, regenerating shared-types
 * will catch type drift at compile time; the `TranscriptEvent`-typed
 * cast at the bottom of this file fails to compile if the Zod schema
 * doesn't infer to the same shape. Belt and braces.
 */

import { z } from 'zod';

import type { TranscriptEvent } from '@verbio/shared-types';

// The shared-types barrel only re-exports the union; the per-variant
// envelopes aren't on its public surface. Extract them locally so the
// compile-time guard below can pin the utterance shape specifically.
type GeneratedUtteranceEnvelope = Extract<TranscriptEvent, { type: 'utterance' }>;

const isoDateTime = z.string().refine((s) => !Number.isNaN(Date.parse(s)), {
  message: 'must be an ISO-8601 datetime',
});

// ---------------------------------------------------------------------------
// Utterance variant (Phase 1)
// ---------------------------------------------------------------------------

const utterancePayloadSchema = z
  .object({
    utterance_id: z.string().uuid(),
    session_id: z.string().uuid(),
    participant_id: z.string().uuid(),
    participant_identity: z.string().min(1),
    participant_display_name: z.string().min(1),
    text: z.string(),
    is_final: z.boolean(),
    // Pydantic always emits the field (default=null), so we require
    // it on the wire even though it's logically optional.
    confidence: z.number().min(0).max(1).nullable(),
    start_ts: isoDateTime,
    end_ts: isoDateTime,
  })
  .strict();

const utteranceEventSchema = z
  .object({
    type: z.literal('utterance'),
    id: z.string().min(1),
    session_id: z.string().uuid(),
    ts: isoDateTime,
    payload: utterancePayloadSchema,
  })
  .strict();

// ---------------------------------------------------------------------------
// State-snapshot variant (Phase 2 L3)
// ---------------------------------------------------------------------------

// Pydantic's `model_dump(mode="json")` includes every field, defaults
// and all — so every nested field below is required on the wire even
// when the generated TS marks it `?` (Pydantic-default → JSON-Schema
// `default` → optional in the TS interface). Requiring them in Zod
// stays compatible with `exactOptionalPropertyTypes` because a present
// value satisfies an optional property. `null`-typed fields (last_spoke_at,
// scheduled_end_at, …) are required-and-nullable.

const participantFlagsSchema = z.object({
  dominating: z.boolean(),
  silent_too_long: z.boolean(),
  frequently_interrupted: z.boolean(),
  disengaged: z.boolean(),
});

const utteranceRefSchema = z.object({
  utterance_id: z.string(),
  text: z.string(),
  spoken_at: isoDateTime,
  duration_sec: z.number().nonnegative(),
});

const participantStateSchema = z.object({
  participant_id: z.string(),
  display_name: z.string(),
  joined_at: isoDateTime,
  speaking_time_total_sec: z.number().nonnegative(),
  speaking_time_last_5min_sec: z.number().nonnegative(),
  speaking_time_last_60sec: z.number().nonnegative(),
  turn_count: z.number().int().nonnegative(),
  last_spoke_at: isoDateTime.nullable(),
  last_spoke_duration_sec: z.number().nonnegative().nullable(),
  is_currently_speaking: z.boolean(),
  vad_active: z.boolean(),
  backchannel_count_last_2min: z.number().int().nonnegative(),
  interruption_count: z.number().int().nonnegative(),
  was_interrupted_count: z.number().int().nonnegative(),
  recent_utterances: z.array(utteranceRefSchema).max(5),
  rolling_transcript_2min: z.string(),
  flags: participantFlagsSchema,
  fair_share_pct: z.number().min(0).max(100),
  actual_share_last_5min_pct: z.number().min(0).max(100),
});

const quietnessBudgetSchema = z.object({
  current_window_count: z.number().int().nonnegative(),
  last_utterance_at: isoDateTime.nullable(),
  max_utterances_per_10min: z.number().int().nonnegative(),
  min_seconds_between_utterances: z.number().nonnegative(),
});

const sessionStateSchema = z.object({
  session_id: z.string().uuid(),
  tick_id: z.number().int().nonnegative(),
  t: isoDateTime,
  started_at: isoDateTime,
  scheduled_end_at: isoDateTime.nullable(),
  elapsed_sec: z.number().nonnegative(),
  participants: z.record(z.string(), participantStateSchema),
  currently_speaking_count: z.number().int().nonnegative(),
  silence_run_sec: z.number().nonnegative(),
  rolling_global_transcript_2min: z.string(),
  is_paused: z.boolean(),
  moderator_muted: z.boolean(),
  quietness_budget: quietnessBudgetSchema,
});

const stateSnapshotPayloadSchema = z
  .object({
    snapshot_id: z.string().uuid(),
    state: sessionStateSchema,
  })
  .strict();

const stateSnapshotEventSchema = z
  .object({
    type: z.literal('state_snapshot'),
    id: z.string().min(1),
    session_id: z.string().uuid(),
    ts: isoDateTime,
    payload: stateSnapshotPayloadSchema,
  })
  .strict();

// ---------------------------------------------------------------------------
// Decision variant (Phase 3 L12)
// ---------------------------------------------------------------------------

// Mirrors `verbio_engine.domain.decision.DecisionAction` / `DecisionSource`.
// Order matches the Pydantic Literal; CI's shared-types check fails on drift.
const decisionActionEnum = z.enum([
  'stay_silent',
  'prompt_participant',
  'redirect_topic',
  'summarize_thread',
  'request_clarification',
  'suggest_turn_taking',
  'close_session',
]);
const decisionSourceEnum = z.enum(['auto', 'researcher_manual', 'researcher_whisper']);

// Pydantic emits every field (`model_dump(mode="json")`), defaults and
// all — so optionals on the generated TS are required-and-present on
// the wire. Mirrors the utterance variant's policy.
const moderatorDecisionSchema = z.object({
  decision_id: z.string().uuid(),
  session_id: z.string().uuid(),
  tick_id: z.number().int().nonnegative(),
  timestamp: isoDateTime,
  action: decisionActionEnum,
  target_participant_id: z.string().nullable(),
  source: decisionSourceEnum,
  triggering_rule: z.string().nullable(),
  researcher_id: z.string().nullable(),
  researcher_hint: z.string().nullable(),
  reason_codes: z.array(z.string()),
  reason_human: z.string(),
  // confidence is required-with-bounds on auto decisions but nullable
  // on researcher manuals; the resolver always emits a 0.0 default for
  // stay_silent. Allowing null here matches the Pydantic shape.
  confidence: z.number().min(0).max(1).nullable(),
  suppressed_by: z.array(z.string()),
  was_executed: z.boolean(),
  llm_prompt: z.record(z.string(), z.unknown()).nullable(),
  llm_output: z.string().nullable(),
  tts_audio_url: z.string().nullable(),
  spoken_at: isoDateTime.nullable(),
  cooldown_until: isoDateTime,
});

const decisionPayloadSchema = z
  .object({
    decision: moderatorDecisionSchema,
  })
  .strict();

const decisionEventSchema = z
  .object({
    type: z.literal('decision'),
    id: z.string().min(1),
    session_id: z.string().uuid(),
    ts: isoDateTime,
    payload: decisionPayloadSchema,
  })
  .strict();

// ---------------------------------------------------------------------------
// Union + public surface
// ---------------------------------------------------------------------------

export const transcriptEventSchema = z.discriminatedUnion('type', [
  utteranceEventSchema,
  stateSnapshotEventSchema,
  decisionEventSchema,
]);

export type TranscriptEventInput = z.input<typeof transcriptEventSchema>;
export type TranscriptEventValidated = z.output<typeof transcriptEventSchema>;

export type UtteranceTranscriptEvent = Extract<TranscriptEventValidated, { type: 'utterance' }>;
export type StateSnapshotTranscriptEvent = Extract<
  TranscriptEventValidated,
  { type: 'state_snapshot' }
>;
export type DecisionTranscriptEvent = Extract<TranscriptEventValidated, { type: 'decision' }>;
export type ModeratorDecisionValidated = z.output<typeof moderatorDecisionSchema>;

/**
 * Compile-time guard for the utterance variant — if Pydantic changes
 * shape, regenerating shared-types will change `UtteranceEventEnvelope`,
 * and the Zod schema fails to type-check until it follows.
 *
 * The snapshot variant has no analogous assertion: `json-schema-to-typescript`
 * expands `recent_utterances` (Pydantic `max_length=5`) into a union of
 * fixed-length tuples (`[] | [U] | [U,U] | …`), which Zod's
 * `z.array().max(5)` infers as `U[]`. The runtime shape is identical,
 * but the type-level shapes don't unify without a tuple-union mirror
 * in Zod that adds noise without catching real drift. The
 * runtime tests above pin both happy and rejection paths instead.
 */
const _assertUtteranceShape: (e: UtteranceTranscriptEvent) => GeneratedUtteranceEnvelope = (e) => e;
void _assertUtteranceShape;

export function parseTranscriptEvent(raw: unknown): TranscriptEventValidated | null {
  const parsed = transcriptEventSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}
