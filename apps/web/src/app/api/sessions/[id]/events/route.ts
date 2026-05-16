/**
 * GET /api/sessions/[id]/events — SSE event stream.
 *
 * Browser dashboards consume this via `EventSource`. The wire format
 * is one JSON-encoded envelope per SSE message, with the envelope's
 * `id` field also set as the SSE `id:` line and the variant's `type`
 * field set as the SSE `event:` line so the client can route via
 * `addEventListener('utterance', …)` / `addEventListener('state_snapshot', …)`.
 *
 * Variants today (P2 L4):
 *   - `utterance`      — one STT segment (Phase 1)
 *   - `state_snapshot` — full SessionState frozen at one tick (Phase 2 L3)
 *
 * Order of operations on connect:
 *   1. Auth + session lookup. 404 if the session is unknown.
 *   2. Subscribe to Redis FIRST and buffer messages that arrive
 *      while we backfill. Subscribing before the DB read avoids the
 *      race where a row is persisted-and-published between our
 *      backfill SELECT and our SUBSCRIBE.
 *   3. Stream backfill rows from Postgres in stable order for each
 *      variant, shaped into the canonical envelope.
 *   4. Drain any buffered Redis messages (de-duplicated against
 *      backfill via per-variant `seenIds`), then flip to live mode.
 *   5. Heartbeat with a `:keepalive` comment every 25s so proxies
 *      (Vercel, Cloudflare) don't time out the idle stream.
 *   6. Tear down subscriber + interval when `req.signal` aborts.
 *
 * `Last-Event-ID` is the most recent envelope id of EITHER variant;
 * we resolve it against both tables on backfill. The two id spaces
 * never collide (utterance UUIDs vs state_snapshot UUIDs) and the
 * cross-session smuggling check inside each repo guards integrity.
 *
 * The route runs on Node.js, not Edge — ioredis is a Node TCP client
 * and Vercel Edge runtime forbids raw sockets. `maxDuration = 300` is
 * Vercel's 5-minute streaming cap for the hobby/pro tier; sessions
 * longer than 5 minutes rely on `EventSource` auto-reconnect with
 * Last-Event-ID, which is built into every browser.
 */

import {
  findSessionById,
  listStateSnapshotsSince,
  listUtterancesSince,
  parseTranscriptEvent,
} from '@/features/sessions';
import type {
  StateSnapshotRow,
  TranscriptEventInput,
  TranscriptEventValidated,
  UtteranceWithSpeakerRow,
} from '@/features/sessions';
import { auth } from '@/lib/auth';
import { createSubscriber, eventsChannel } from '@/lib/redis';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
// Vercel streaming cap. EventSource handles reconnects beyond this.
export const maxDuration = 300;

const HEARTBEAT_MS = 25_000;
const UTTERANCE_BACKFILL_LIMIT = 500;
const SNAPSHOT_BACKFILL_LIMIT = 240; // 2 min @ 2 Hz; matches repo default.

interface RouteContext {
  params: Promise<{ id: string }>;
}

export async function GET(request: Request, context: RouteContext): Promise<Response> {
  const userSession = await auth();
  if (!userSession?.user) {
    return new Response(JSON.stringify({ error: 'unauthorized' }), {
      status: 401,
      headers: { 'content-type': 'application/json' },
    });
  }

  const { id: sessionId } = await context.params;
  const session = await findSessionById(sessionId);
  if (session === null) {
    return new Response(JSON.stringify({ error: 'session_not_found' }), {
      status: 404,
      headers: { 'content-type': 'application/json' },
    });
  }

  const lastEventId = request.headers.get('last-event-id') ?? undefined;
  const channel = eventsChannel(sessionId);

  // Captured by both `start` (to enqueue) and `cancel` (to tear down)
  // so the consumer aborting the reader cleans up Redis + intervals
  // even when the underlying Request has no AbortController attached.
  let teardown: (() => Promise<void>) | null = null;

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const encoder = new TextEncoder();
      const seenIds = new Set<string>();
      const buffered: string[] = [];
      let liveMode = false;
      let closed = false;
      let heartbeat: ReturnType<typeof setInterval> | null = null;

      const subscriber = createSubscriber();

      const safeEnqueue = (chunk: string): void => {
        if (closed) return;
        try {
          controller.enqueue(encoder.encode(chunk));
        } catch {
          // Controller may already be closed by an abort racing with
          // a publish; swallow rather than crash the publisher loop.
          closed = true;
        }
      };

      const writeEvent = (event: TranscriptEventInput): void => {
        if (seenIds.has(event.id)) return;
        seenIds.add(event.id);
        const data = JSON.stringify(event);
        // The `event:` line lets the browser dispatch to a typed
        // listener; both variants are routed through the same SSE
        // stream so a single EventSource sees everything.
        safeEnqueue(`id: ${event.id}\nevent: ${event.type}\ndata: ${data}\n\n`);
      };

      const onMessage = (channelName: string, payload: string): void => {
        if (channelName !== channel) return;
        if (!liveMode) {
          buffered.push(payload);
          return;
        }
        const parsed = parseTranscriptEvent(safeParse(payload));
        if (parsed !== null) writeEvent(parsed);
      };

      const close = async (): Promise<void> => {
        if (closed) return;
        closed = true;
        if (heartbeat !== null) {
          clearInterval(heartbeat);
          heartbeat = null;
        }
        subscriber.off('message', onMessage);
        try {
          await subscriber.unsubscribe(channel);
        } catch {
          // Subscriber may already be down; nothing actionable.
        }
        try {
          subscriber.disconnect();
        } catch {
          // ditto
        }
        try {
          controller.close();
        } catch {
          // Already closed.
        }
      };

      teardown = close;
      request.signal.addEventListener('abort', () => {
        void close();
      });

      // 1. Subscribe FIRST so messages published during backfill are
      //    captured into `buffered` and replayed after backfill.
      subscriber.on('message', onMessage);
      try {
        await subscriber.subscribe(channel);
      } catch (err) {
        safeEnqueue(
          `event: error\ndata: ${JSON.stringify({
            error: 'redis_subscribe_failed',
            message: err instanceof Error ? err.message : 'unknown',
          })}\n\n`,
        );
        await close();
        return;
      }

      // Hello frame — lets the client log a successful connect and
      // confirms the route is wired before any silence.
      safeEnqueue(`event: ready\ndata: ${JSON.stringify({ session_id: sessionId })}\n\n`);

      // 2. Backfill both variants in parallel. The cursor (`lastEventId`)
      //    might be either an utterance UUID or a snapshot UUID; each
      //    repo's cross-session check silently drops cursors that don't
      //    belong to this session, so passing the same id to both is
      //    safe — at most one repo finds the cursor, the other returns
      //    from the start.
      const [utteranceBackfill, snapshotBackfill] = await Promise.all([
        listUtterancesSince(sessionId, {
          ...(lastEventId !== undefined && { afterUtteranceId: lastEventId }),
          limit: UTTERANCE_BACKFILL_LIMIT,
        }),
        listStateSnapshotsSince(sessionId, {
          ...(lastEventId !== undefined && { afterSnapshotId: lastEventId }),
          limit: SNAPSHOT_BACKFILL_LIMIT,
        }),
      ]);

      for (const row of utteranceBackfill) {
        writeEvent(utteranceRowToEvent(row));
      }
      for (const row of snapshotBackfill) {
        const event = snapshotRowToEvent(row);
        if (event !== null) writeEvent(event);
      }

      // 3. Drain buffered live messages and flip to live mode.
      for (const payload of buffered) {
        const parsed = parseTranscriptEvent(safeParse(payload));
        if (parsed !== null) writeEvent(parsed);
      }
      buffered.length = 0;
      liveMode = true;

      // 4. Heartbeat so proxies keep the stream open.
      heartbeat = setInterval(() => {
        safeEnqueue(`:keepalive\n\n`);
      }, HEARTBEAT_MS);
    },
    async cancel() {
      if (teardown !== null) await teardown();
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache, no-transform',
      connection: 'keep-alive',
      // Disable nginx/Vercel response buffering — every chunk must
      // flush immediately or SSE devolves into chunked polling.
      'x-accel-buffering': 'no',
    },
  });
}

function safeParse(payload: string): unknown {
  try {
    return JSON.parse(payload);
  } catch {
    return null;
  }
}

/**
 * Shape a backfilled DB row as a `TranscriptEvent` so the client
 * sees one wire shape regardless of whether the event came live from
 * Redis or replayed from Postgres. `ts` is the server-side moment we
 * synthesised the event, matching the engine convention.
 */
function utteranceRowToEvent(row: UtteranceWithSpeakerRow): TranscriptEventInput {
  return {
    type: 'utterance',
    id: row.id,
    session_id: row.sessionId,
    ts: new Date().toISOString(),
    payload: {
      utterance_id: row.id,
      session_id: row.sessionId,
      participant_id: row.participantId,
      participant_identity: row.participantIdentity,
      participant_display_name: row.participantDisplayName,
      text: row.text,
      is_final: row.isFinal,
      confidence: row.confidence,
      start_ts: row.startTs.toISOString(),
      end_ts: row.endTs.toISOString(),
    },
  };
}

/**
 * Shape a backfilled `state_snapshots` row as a transcript envelope.
 *
 * The `state` column is JSONB containing what Pydantic's
 * `model_dump(mode="json")` produced at tick time. We round-trip it
 * through the same Zod schema the live publish path uses so the
 * dashboard sees only validated frames — a corrupt or partially-written
 * snapshot is dropped (returns null) rather than poisoning the stream.
 */
function snapshotRowToEvent(row: StateSnapshotRow): TranscriptEventValidated | null {
  return parseTranscriptEvent({
    type: 'state_snapshot',
    id: row.id,
    session_id: row.sessionId,
    ts: row.ts.toISOString(),
    payload: {
      snapshot_id: row.id,
      state: row.state,
    },
  });
}
