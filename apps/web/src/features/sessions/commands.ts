/**
 * Researcher command bus — web producer (P5 L2).
 *
 * The web side publishes `ResearcherCommand` envelopes onto a per-session
 * Redis stream (`verbio:commands:{session_id}`); the engine drains the
 * stream at the top of each tick (brief §6 step 2). Two surfaces share
 * the wire shape: Pydantic on the engine, the generated TS interface in
 * `@verbio/shared-types`, and the Zod schemas below for runtime
 * validation of inbound HTTP requests.
 *
 * The schemas here intentionally mirror the typed payload classes in
 * `verbio_engine/domain/command.py` field-for-field. The pre-commit
 * `shared-types:freshness` check guards the generated TS against drift;
 * the Zod schemas below have no automated drift detector, so any change
 * to the engine payload classes MUST be reflected here in the same PR.
 *
 * Idempotency: the wire `command_id` is the engine's primary key on
 * `researcher_actions`. A retry that re-publishes the same `command_id`
 * is a no-op at the persistence boundary, so the producer side is free
 * to retry XADD on transient Redis errors without dedup state.
 */

import 'server-only';

import { randomUUID } from 'node:crypto';

import { z } from 'zod';

import { COMMAND_STREAM_MAXLEN, commandsStreamKey, getRedis } from '@/lib/redis';

import type { ResearcherCommand } from '@verbio/shared-types';

import { SessionAlreadyEndedError, SessionNotFoundError } from './errors';
import { findSessionById } from './repo';

// ---------------------------------------------------------------------------
// Typed payload schemas — mirror verbio_engine.domain.command
// ---------------------------------------------------------------------------

const promptText = z.string().trim().min(1).max(2000);
const noteText = z.string().trim().min(1).max(1000);
const reasonText = z.string().trim().min(1).max(500);

const forcePromptPayloadSchema = z
  .object({
    prompt: promptText,
    target_participant_id: z.string().uuid().nullable().optional(),
  })
  .strict();

const forceRedirectPayloadSchema = z
  .object({
    topic: promptText,
  })
  .strict();

const forceSummaryPayloadSchema = z
  .object({
    focus: promptText.nullable().optional(),
  })
  .strict();

const whisperPayloadSchema = z
  .object({
    text: promptText,
    target_participant_id: z.string().uuid().nullable().optional(),
  })
  .strict();

const emptyPayloadSchema = z.object({}).strict();

const setQuietnessBudgetPayloadSchema = z
  .object({
    max_utterances_per_10min: z.number().int().positive().nullable().optional(),
    min_seconds_between_utterances: z.number().nonnegative().nullable().optional(),
  })
  .strict()
  .refine(
    (v) =>
      (v.max_utterances_per_10min ?? null) !== null ||
      (v.min_seconds_between_utterances ?? null) !== null,
    {
      message:
        'set_quietness_budget payload must set at least one of ' +
        'max_utterances_per_10min, min_seconds_between_utterances',
    },
  );

const flagMomentPayloadSchema = z
  .object({
    note: noteText.nullable().optional(),
  })
  .strict();

const endSessionPayloadSchema = z
  .object({
    reason: reasonText.nullable().optional(),
  })
  .strict();

// ---------------------------------------------------------------------------
// Input schema — what the POST route accepts from the dashboard
// ---------------------------------------------------------------------------

/**
 * Per-command-type input shape. `command_id` is minted server-side (not
 * accepted from the client) so that a misbehaving UI can't collide IDs
 * across researchers; `session_id`, `researcher_id`, and `issued_at` are
 * also stamped server-side from the route context. The client only
 * supplies `command_type` + the typed `payload`.
 */
export const PublishCommandInputSchema = z.discriminatedUnion('command_type', [
  z.object({ command_type: z.literal('force_prompt'), payload: forcePromptPayloadSchema }),
  z.object({ command_type: z.literal('force_redirect'), payload: forceRedirectPayloadSchema }),
  z.object({
    command_type: z.literal('force_summary'),
    payload: forceSummaryPayloadSchema.optional().default({}),
  }),
  z.object({ command_type: z.literal('whisper'), payload: whisperPayloadSchema }),
  z.object({
    command_type: z.literal('mute_moderator'),
    payload: emptyPayloadSchema.optional().default({}),
  }),
  z.object({
    command_type: z.literal('unmute_moderator'),
    payload: emptyPayloadSchema.optional().default({}),
  }),
  z.object({
    command_type: z.literal('pause_session'),
    payload: emptyPayloadSchema.optional().default({}),
  }),
  z.object({
    command_type: z.literal('resume_session'),
    payload: emptyPayloadSchema.optional().default({}),
  }),
  z.object({
    command_type: z.literal('set_quietness_budget'),
    payload: setQuietnessBudgetPayloadSchema,
  }),
  z.object({
    command_type: z.literal('flag_moment'),
    payload: flagMomentPayloadSchema.optional().default({}),
  }),
  z.object({
    command_type: z.literal('end_session'),
    payload: endSessionPayloadSchema.optional().default({}),
  }),
]);

export type PublishCommandInput = z.infer<typeof PublishCommandInputSchema>;

interface PublishArgs {
  sessionId: string;
  researcherId: string;
  input: PublishCommandInput;
}

interface PublishResult {
  commandId: string;
  issuedAt: string;
  streamEntryId: string;
}

/**
 * Publish a researcher command onto the engine's per-session command
 * stream. Returns the minted `command_id` plus the Redis stream entry id
 * so the UI can correlate optimistic state with the engine's eventual
 * decision (which carries the same `command_id` as `researcher_id`'s
 * trail in `researcher_actions` and `decisions.researcher_hint`).
 *
 * Rejects with `SessionNotFoundError` / `SessionAlreadyEndedError` so
 * the route handler can map to 404 / 409 with the same translation it
 * already does for `start`.
 */
export async function publishResearcherCommand({
  sessionId,
  researcherId,
  input,
}: PublishArgs): Promise<PublishResult> {
  const session = await findSessionById(sessionId);
  if (session === null) throw new SessionNotFoundError(sessionId);
  if (session.status === 'ended' || session.status === 'aborted') {
    throw new SessionAlreadyEndedError(sessionId);
  }

  const commandId = randomUUID();
  const issuedAt = new Date().toISOString();

  // Wire-shape mirrors `verbio_engine.domain.command.ResearcherCommand`.
  // Payload is the validated discriminated-union body; defaults expanded
  // by Zod's `.default({})` ensure command_types without per-call args
  // still serialise as `{}` on the wire (matches engine's empty-dict expectation).
  const envelope: ResearcherCommand = {
    command_id: commandId,
    session_id: sessionId,
    researcher_id: researcherId,
    issued_at: issuedAt,
    command_type: input.command_type,
    payload: input.payload,
  };

  const redis = getRedis();
  // `MAXLEN ~ N` is approximate-trim; redis nudges the trim to a radix
  // boundary for efficiency. The engine's drain-from-"0"-on-restart
  // model relies on this cap to stay bounded if the engine is offline.
  const streamEntryId = await redis.xadd(
    commandsStreamKey(sessionId),
    'MAXLEN',
    '~',
    String(COMMAND_STREAM_MAXLEN),
    '*',
    'data',
    JSON.stringify(envelope),
  );
  // ioredis types `xadd`'s return as `string | null`; in practice null
  // only surfaces when XADD is called with NOMKSTREAM on a missing
  // stream, which we don't do. Defensive guard so the contract holds.
  if (streamEntryId === null) {
    throw new Error('redis.xadd returned null — stream entry was not appended');
  }

  return { commandId, issuedAt, streamEntryId };
}
