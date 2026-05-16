/**
 * Runtime-validating Zod schemas for the SSE event envelopes.
 *
 * The TypeScript shape is generated from Pydantic in `@verbio/shared-types`
 * (`TranscriptEvent`); this file pairs that with a Zod schema so the
 * web side can VALIDATE the wire — generated TS types only describe
 * shape, they don't enforce it at runtime, and an event that doesn't
 * match the schema would otherwise reach the dashboard as malformed
 * JSON.
 *
 * If the engine's Pydantic shape changes, regenerating shared-types
 * will catch type drift at compile time; the `TranscriptEvent`-typed
 * cast at the bottom of this file fails to compile if the Zod schema
 * doesn't infer to the same shape. Belt and braces.
 */

import { z } from 'zod';

import type { TranscriptEvent } from '@verbio/shared-types';

const isoDateTime = z.string().refine((s) => !Number.isNaN(Date.parse(s)), {
  message: 'must be an ISO-8601 datetime',
});

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

export const transcriptEventSchema = z
  .object({
    type: z.literal('utterance'),
    id: z.string().min(1),
    session_id: z.string().uuid(),
    ts: isoDateTime,
    payload: utterancePayloadSchema,
  })
  .strict();

export type TranscriptEventInput = z.input<typeof transcriptEventSchema>;
export type TranscriptEventValidated = z.output<typeof transcriptEventSchema>;

/**
 * Compile-time guard: a Zod-validated event must be assignable to the
 * generated TS type. If Pydantic changes shape, regenerating shared-types
 * will change `TranscriptEvent`; if the Zod schema here doesn't follow,
 * this function reference fails to type-check.
 */
const _assertAssignable: (e: TranscriptEventValidated) => TranscriptEvent = (e) => e;
void _assertAssignable;

export function parseTranscriptEvent(raw: unknown): TranscriptEventValidated | null {
  const parsed = transcriptEventSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}
