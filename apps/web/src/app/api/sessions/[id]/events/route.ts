/**
 * GET /api/sessions/[id]/events — SSE transcript stream.
 *
 * Browser dashboards consume this via `EventSource`. The wire format
 * is one JSON-encoded `TranscriptEvent` per SSE message, with the
 * event's `id` field also set as the SSE `id:` line. When the
 * connection drops, the browser reconnects with `Last-Event-ID`; we
 * backfill any rows the client missed from Postgres before resuming
 * Redis pub/sub. This is the dashboard's "live transcript" surface
 * mandated by Phase 1 of the brief (§14: "observe live transcript
 * only — no moderator, no state, no decisions yet").
 *
 * Order of operations on connect:
 *   1. Auth + session lookup. 404 if the session is unknown.
 *   2. Subscribe to Redis FIRST and buffer messages that arrive
 *      while we backfill. Subscribing before the DB read avoids the
 *      race where an utterance is persisted-and-published between
 *      our backfill SELECT and our SUBSCRIBE.
 *   3. Stream backfill rows from Postgres in `(start_ts, id)` order,
 *      shaped into the canonical `TranscriptEvent` envelope.
 *   4. Drain any buffered Redis messages (de-duplicated against
 *      backfill via `seenIds`), then flip to live mode.
 *   5. Heartbeat with a `:keepalive` comment every 25s so proxies
 *      (Vercel, Cloudflare) don't time out the idle stream.
 *   6. Tear down subscriber + interval when `req.signal` aborts.
 *
 * The route runs on Node.js, not Edge — ioredis is a Node TCP client
 * and Vercel Edge runtime forbids raw sockets. `maxDuration = 300` is
 * Vercel's 5-minute streaming cap for the hobby/pro tier; sessions
 * longer than 5 minutes rely on `EventSource` auto-reconnect with
 * Last-Event-ID, which is built into every browser.
 */

import { findSessionById, listUtterancesSince, parseTranscriptEvent } from '@/features/sessions';
import type { TranscriptEventInput, UtteranceWithSpeakerRow } from '@/features/sessions';
import { auth } from '@/lib/auth';
import { createSubscriber, eventsChannel } from '@/lib/redis';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
// Vercel streaming cap. EventSource handles reconnects beyond this.
export const maxDuration = 300;

const HEARTBEAT_MS = 25_000;
const BACKFILL_LIMIT = 500;

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
        safeEnqueue(`id: ${event.id}\nevent: utterance\ndata: ${data}\n\n`);
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

      // 2. Backfill from Postgres in stable order.
      const backfill = await listUtterancesSince(sessionId, {
        ...(lastEventId !== undefined && { afterUtteranceId: lastEventId }),
        limit: BACKFILL_LIMIT,
      });
      for (const row of backfill) {
        writeEvent(utteranceRowToEvent(row));
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
