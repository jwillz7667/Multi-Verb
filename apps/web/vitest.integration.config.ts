/**
 * Vitest config for integration tests (P7 L3).
 *
 * Integration tests live under `test/integration/` and hit a real
 * Postgres via Prisma — no jsdom, no mocks of the data layer. The main
 * `vitest.config.ts` excludes this directory so `pnpm test` stays a
 * pure unit run that requires no infrastructure.
 *
 * To run:
 *   docker compose -f infra/docker-compose.dev.yml up -d postgres
 *   cd services/engine && \
 *     DATABASE_URL_DIRECT='postgresql+asyncpg://verbio:verbio@localhost:5432/verbio' \
 *     uv run alembic upgrade head
 *   cd apps/web && \
 *     INTEGRATION_DATABASE_URL='postgresql://verbio:verbio@localhost:5432/verbio' \
 *     pnpm test:integration
 *
 * When `INTEGRATION_DATABASE_URL` is unset the suite skips itself via
 * `describe.skipIf` — keeping the script callable everywhere without
 * blocking on infra.
 */

import { resolve } from 'node:path';

import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    alias: {
      '@': resolve(import.meta.dirname, './src'),
    },
  },
  test: {
    // `node` (not `jsdom`) — Prisma client is server-only and this
    // suite never touches React. Also keeps the test runtime
    // representative of the serverless route handlers that scopedDb
    // actually serves.
    environment: 'node',
    globals: true,
    setupFiles: ['./test/integration/setup.ts'],
    include: ['test/integration/**/*.test.ts'],
    // Single fork — Prisma client connection setup is per-process and
    // serial seed/teardown is the simplest contract. Parallelism would
    // need per-suite schema namespacing which is overkill for one file.
    pool: 'forks',
    poolOptions: { forks: { singleFork: true } },
    // Integration tests inevitably wait on the network + Postgres.
    // Generous timeout so a slow CI runner doesn't flake the suite.
    testTimeout: 30_000,
    hookTimeout: 30_000,
  },
});
