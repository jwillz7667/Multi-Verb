/**
 * Redis client singletons for SSE fan-out and command publishing.
 *
 * Two long-lived pools per process:
 *   - `getRedis()` — general-purpose client (commands, future researcher
 *     command publishing). Pub/sub subscribers cannot share this — the
 *     Redis protocol disallows non-subscribe commands on a subscribed
 *     connection.
 *   - `createSubscriber()` — per-SSE-connection subscriber, returned
 *     fresh and torn down when the request aborts.
 *
 * Hot-reload safety: Next.js dev mode reloads modules on file change,
 * which would otherwise create a fresh Redis pool every time and leak
 * connections. Stashing the instance on `globalThis` mirrors the
 * `prisma` singleton pattern in `db.ts`.
 *
 * Channel naming mirrors the engine's `verbio_engine.realtime.channel_for`;
 * the two surfaces must agree or events vanish into the void.
 */

import 'server-only';

import Redis from 'ioredis';

import { serverEnv } from '@/lib/env';

declare global {
  var __verbioRedis: Redis | undefined;
}

function build(): Redis {
  if (serverEnv === null) {
    throw new Error('Redis client requires server-side env');
  }
  return new Redis(serverEnv.REDIS_URL, {
    // Lazy connect so a slow Redis doesn't block module import (which
    // would gate the whole Next.js route compilation). The first
    // command opens the socket; ioredis queues anything until then.
    lazyConnect: false,
    // Be permissive with reconnects — a flaky Redis shouldn't kill
    // ongoing SSE streams. The subscriber path also has its own
    // reconnect-and-resubscribe handling inside the route.
    maxRetriesPerRequest: 3,
    enableReadyCheck: true,
    // Keep the connection name short and human-greppable for `CLIENT LIST`.
    connectionName: 'verbio-web',
  });
}

export function getRedis(): Redis {
  globalThis.__verbioRedis ??= build();
  return globalThis.__verbioRedis;
}

/**
 * Construct a fresh subscriber connection.
 *
 * The Redis protocol restricts a subscribed connection to PUB/SUB
 * commands, so we cannot share `getRedis()` with subscribers. Each SSE
 * request owns its subscriber and disconnects it when the client goes
 * away.
 */
export function createSubscriber(): Redis {
  return getRedis().duplicate();
}

/**
 * Canonical channel name — must match the engine's
 * `verbio_engine.realtime.channel_for(session_id)`.
 */
export function eventsChannel(sessionId: string): string {
  return `verbio:events:${sessionId}`;
}
