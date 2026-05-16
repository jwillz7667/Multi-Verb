/**
 * Playwright-side Prisma client.
 *
 * `@/lib/db` imports the `server-only` shim, which throws at module
 * load time outside a React Server Component context — including
 * Playwright workers. So globalSetup and specs can't use it directly.
 * This module exists purely as the test-runner-safe counterpart: it
 * constructs a Prisma client from the same generated bindings, using
 * the same `DATABASE_URL_POOLED` env var the production runtime uses.
 *
 * Hot-reload doesn't apply here — Playwright workers are short-lived
 * — so we don't bother with the globalThis caching trick `@/lib/db`
 * uses.
 */

import { PrismaClient } from '@/generated/prisma';

export const db = new PrismaClient({
  log: ['error'],
});
