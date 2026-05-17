/**
 * LiveKit egress webhook processing.
 *
 * LiveKit Cloud writes recordings server-side directly to R2 (the
 * engine just hands it credentials at start time — see the engine-side
 * `LiveKitEgressDispatcher`). Completion arrives here as an
 * `egress_ended` webhook, and this module's job is to persist the R2
 * keys onto the matching `sessions` row so the replay UI can mint
 * signed URLs without re-asking LiveKit.
 *
 * Design choices, with the reasoning the brief leaves to us:
 *
 * * **Session lookup by room name, not egress_id.** The engine doesn't
 *   persist a `(session_id, egress_id)` mapping at start time — the
 *   handle is logged but not stored. LiveKit's `EgressInfo` carries the
 *   `room_name`, and `sessions.livekit_room_name` is unique, so that's
 *   our join key. Adds zero new state vs. an egress-id lookup table.
 *
 * * **200 on every well-formed signature.** Non-egress events, ended
 *   events for sessions we've never seen, ended events with
 *   `EGRESS_FAILED` status — all return 200. LiveKit's webhook delivery
 *   retries 5xx; a 4xx won't bring the recording back, and an unknown
 *   room is genuinely a we-already-deleted-the-session situation we
 *   want to acknowledge and forget. Signature failure (401) and missing
 *   creds (503) are the only non-200 outcomes.
 *
 * * **Atomic merge for participant URLs.** Multiple
 *   `start_participant_egress` calls per session means multiple
 *   participant `egress_ended` events firing concurrently. A naive
 *   read-merge-write races; we use Postgres `jsonb || jsonb_build_object()`
 *   so two parallel updates compose instead of clobber.
 *
 * * **Composite write is naturally idempotent.** A duplicate webhook
 *   for the same composite egress writes the same `recording_url`
 *   value. No version column or `if_null` guard needed — the brief
 *   asks for idempotency, and the data model gives it for free.
 *
 * * **Repo abstraction over Prisma.** The processor depends on a small
 *   `WebhookRecordingsRepo` interface, not the Prisma client directly,
 *   so unit tests can drive every branch without `vi.mock`-ing the
 *   tagged-template `$executeRaw`. Production wiring constructs the
 *   Prisma-backed impl from the `db` singleton.
 */

import 'server-only';

import { EgressStatus } from 'livekit-server-sdk';

import { Prisma } from '@/generated/prisma';
import type { PrismaClient } from '@/generated/prisma';
import { db } from '@/lib/db';
import { logger as rootLogger } from '@/lib/logger';

import type { WebhookEvent } from 'livekit-server-sdk';

const EGRESS_FAILURE_STATUSES: ReadonlySet<EgressStatus> = new Set([
  EgressStatus.EGRESS_FAILED,
  EgressStatus.EGRESS_ABORTED,
  EgressStatus.EGRESS_LIMIT_REACHED,
]);

/**
 * Discriminated outcome of processing one webhook envelope.
 *
 * Every variant is a "200 OK" from the route's perspective — including
 * `unknown_room` and `egress_failed`. We branch in the route only to
 * emit a structured log; the wire response is a single shape.
 */
export type ProcessResult =
  | { kind: 'ignored'; reason: IgnoreReason; event: string }
  | { kind: 'unknown_room'; roomName: string }
  | { kind: 'persisted_composite'; sessionId: string; r2Key: string }
  | { kind: 'persisted_participant'; sessionId: string; identity: string; r2Key: string }
  | { kind: 'egress_failed'; sessionId: string; egressKind: string; status: number; error: string };

export type IgnoreReason =
  | 'non_egress_event'
  | 'non_terminal_egress_event'
  | 'missing_egress_info'
  | 'missing_room_name'
  | 'unsupported_request_kind'
  | 'no_file_results'
  | 'missing_participant_identity';

/**
 * Persistence surface the processor depends on. Kept narrow so tests
 * can supply an in-memory fake without faking the whole Prisma client.
 */
export interface WebhookRecordingsRepo {
  /** Resolve the moderated_session.id for a given livekit_room_name, or null. */
  findSessionIdByRoomName(roomName: string): Promise<string | null>;
  /** Persist the composite recording's R2 key onto the sessions row. */
  setCompositeRecordingUrl(sessionId: string, r2Key: string): Promise<void>;
  /**
   * Merge `{ [identity]: r2Key }` into `per_participant_recording_urls`
   * atomically. Multiple participant egresses may complete concurrently;
   * this MUST be a single SQL statement against jsonb, not a read-merge-write.
   */
  upsertParticipantRecordingUrl(sessionId: string, identity: string, r2Key: string): Promise<void>;
}

export interface ProcessLogger {
  info: (msg: string, ctx?: Record<string, unknown>) => void;
  warn: (msg: string, ctx?: Record<string, unknown>) => void;
  debug: (msg: string, ctx?: Record<string, unknown>) => void;
}

export interface ProcessDeps {
  repo: WebhookRecordingsRepo;
  logger?: ProcessLogger;
}

/**
 * Pure processor: takes a verified `WebhookEvent`, mutates persistence
 * through the repo, returns a typed outcome. Never throws on
 * data-shape oddities — they map to `ignored` / `unknown_room` so the
 * webhook returns 200 and LiveKit doesn't retry pointlessly.
 *
 * The caller (route handler) is responsible for signature verification
 * BEFORE invoking this — the event arrives here already trusted.
 */
export async function processEgressWebhookEvent(
  event: WebhookEvent,
  deps: ProcessDeps,
): Promise<ProcessResult> {
  const logger = deps.logger ?? consoleLogger;
  const name = event.event;

  if (name !== 'egress_started' && name !== 'egress_updated' && name !== 'egress_ended') {
    logger.debug('livekit_webhook.ignored_non_egress', { event: name, id: event.id });
    return { kind: 'ignored', reason: 'non_egress_event', event: name };
  }

  if (name !== 'egress_ended') {
    // egress_started / egress_updated are progress signals — useful in
    // a future ops dashboard, but they don't move our state machine
    // forward (we already wrote the R2 key plan at start time).
    logger.debug('livekit_webhook.ignored_egress_progress', {
      event: name,
      id: event.id,
      egressId: event.egressInfo?.egressId,
    });
    return { kind: 'ignored', reason: 'non_terminal_egress_event', event: name };
  }

  const info = event.egressInfo;
  if (info === undefined) {
    logger.warn('livekit_webhook.missing_egress_info', { event: name, id: event.id });
    return { kind: 'ignored', reason: 'missing_egress_info', event: name };
  }

  const roomName = info.roomName;
  if (roomName === '') {
    logger.warn('livekit_webhook.missing_room_name', {
      event: name,
      egressId: info.egressId,
    });
    return { kind: 'ignored', reason: 'missing_room_name', event: name };
  }

  const sessionId = await deps.repo.findSessionIdByRoomName(roomName);
  if (sessionId === null) {
    logger.warn('livekit_webhook.unknown_room', {
      roomName,
      egressId: info.egressId,
    });
    return { kind: 'unknown_room', roomName };
  }

  const requestCase = info.request.case;
  if (requestCase !== 'roomComposite' && requestCase !== 'participant') {
    logger.debug('livekit_webhook.unsupported_request_kind', {
      requestCase,
      egressId: info.egressId,
      sessionId,
    });
    return { kind: 'ignored', reason: 'unsupported_request_kind', event: name };
  }

  if (EGRESS_FAILURE_STATUSES.has(info.status)) {
    // Log loud-ish (warn) so ops can triage. We do NOT persist a partial
    // r2_key — the file in R2 is incomplete or absent, and the replay
    // UI's "no recording" branch is the correct surface.
    logger.warn('livekit_webhook.egress_failed', {
      sessionId,
      egressId: info.egressId,
      egressKind: requestCase,
      status: info.status,
      error: info.error,
      errorCode: info.errorCode,
    });
    return {
      kind: 'egress_failed',
      sessionId,
      egressKind: requestCase,
      status: info.status,
      error: info.error,
    };
  }

  if (info.status !== EgressStatus.EGRESS_COMPLETE) {
    // `egress_ended` with a non-terminal status would be a LiveKit bug;
    // log + skip rather than crash.
    logger.warn('livekit_webhook.unexpected_status_on_ended', {
      sessionId,
      egressId: info.egressId,
      status: info.status,
    });
    return { kind: 'ignored', reason: 'non_terminal_egress_event', event: name };
  }

  const firstFile = info.fileResults[0];
  if (firstFile === undefined || firstFile.filename === '') {
    logger.warn('livekit_webhook.no_file_results', {
      sessionId,
      egressId: info.egressId,
      egressKind: requestCase,
    });
    return { kind: 'ignored', reason: 'no_file_results', event: name };
  }

  // `filename` is the R2 object key the engine handed LiveKit at start
  // time (`sessions/<uuid>/composite.mp4` etc.). Persisting the key —
  // not a signed URL — keeps freshness in the read path: the replay
  // endpoint mints a new URL with the right TTL on every fetch.
  const r2Key = firstFile.filename;

  if (requestCase === 'roomComposite') {
    await deps.repo.setCompositeRecordingUrl(sessionId, r2Key);
    logger.info('livekit_webhook.composite_persisted', {
      sessionId,
      egressId: info.egressId,
      r2Key,
    });
    return { kind: 'persisted_composite', sessionId, r2Key };
  }

  // requestCase === 'participant'
  const participantReq = info.request.value;
  const identity = participantReq.identity;
  if (identity === '') {
    logger.warn('livekit_webhook.missing_participant_identity', {
      sessionId,
      egressId: info.egressId,
    });
    return { kind: 'ignored', reason: 'missing_participant_identity', event: name };
  }
  await deps.repo.upsertParticipantRecordingUrl(sessionId, identity, r2Key);
  logger.info('livekit_webhook.participant_persisted', {
    sessionId,
    egressId: info.egressId,
    identity,
    r2Key,
  });
  return { kind: 'persisted_participant', sessionId, identity, r2Key };
}

/**
 * Construct the production repo backed by a Prisma client.
 *
 * `upsertParticipantRecordingUrl` issues a single atomic UPDATE using
 * Postgres `jsonb || jsonb_build_object(...)` so concurrent participant
 * egresses don't lose each other's keys via read-modify-write race.
 */
export function createPrismaWebhookRecordingsRepo(client: PrismaClient): WebhookRecordingsRepo {
  return {
    async findSessionIdByRoomName(roomName) {
      const row = await client.moderatedSession.findUnique({
        where: { livekitRoomName: roomName },
        select: { id: true },
      });
      return row?.id ?? null;
    },
    async setCompositeRecordingUrl(sessionId, r2Key) {
      await client.moderatedSession.update({
        where: { id: sessionId },
        data: { recordingUrl: r2Key },
      });
    },
    async upsertParticipantRecordingUrl(sessionId, identity, r2Key) {
      // Tagged-template `$executeRaw` parameterises the interpolated
      // values as $1, $2, $3 — no SQL-injection surface. Casts pin the
      // bind types so Postgres doesn't infer them as `unknown`.
      await client.$executeRaw(
        Prisma.sql`
          UPDATE sessions
          SET per_participant_recording_urls =
            COALESCE(per_participant_recording_urls, '{}'::jsonb)
            || jsonb_build_object(${identity}::text, ${r2Key}::text)
          WHERE id = ${sessionId}::uuid
        `,
      );
    },
  };
}

/**
 * Default singleton repo, bound to the shared Prisma client. Routes
 * import this; tests inject their own.
 */
export const defaultWebhookRecordingsRepo: WebhookRecordingsRepo =
  createPrismaWebhookRecordingsRepo(db);

// Production-default logger when callers don't inject one. Wraps the
// shared structured logger from `lib/logger.ts` so info/debug land at
// their honest levels (rather than being smuggled through warn to
// dodge the no-console preset).
const consoleLogger: ProcessLogger = rootLogger.child({ component: 'recordings.webhook' });
