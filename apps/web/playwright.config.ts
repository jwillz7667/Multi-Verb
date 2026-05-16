/**
 * Playwright configuration for verbio-web E2E.
 *
 * The Phase 1 brief calls for an E2E that "spins up 2 fake
 * participants (pre-recorded audio loops) and asserts utterances
 * land in the DB." That test needs the real engine joined to a
 * LiveKit room with Deepgram STT — substantial infra, so we gate
 * the suite on env presence and bail with a clear skip message
 * when credentials aren't configured.
 *
 * The webServer block wires Next dev mode so `pnpm test:e2e` is
 * self-contained against an already-running engine + LiveKit +
 * Postgres + Redis (brought up via `infra/docker-compose.dev.yml`).
 * Engine boot is the operator's responsibility — Playwright won't
 * spawn a Python process for it; that's better managed by the dev
 * loop or the CI workflow.
 */

import { fileURLToPath } from 'node:url';

import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env['PLAYWRIGHT_WEB_PORT'] ?? 3100);
const BASE_URL = process.env['PLAYWRIGHT_BASE_URL'] ?? `http://127.0.0.1:${PORT}`;

const GLOBAL_SETUP = fileURLToPath(new URL('./tests/e2e/global-setup.ts', import.meta.url));
const STORAGE_STATE = fileURLToPath(new URL('./tests/e2e/.auth/session.json', import.meta.url));

const skipWebServer = process.env['PLAYWRIGHT_SKIP_WEB_SERVER'] === '1';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  forbidOnly: process.env['CI'] === 'true',
  retries: process.env['CI'] === 'true' ? 1 : 0,
  workers: 1,
  reporter: process.env['CI'] === 'true' ? [['list'], ['html', { open: 'never' }]] : 'list',
  timeout: 90_000,
  expect: { timeout: 20_000 },
  globalSetup: GLOBAL_SETUP,
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    storageState: STORAGE_STATE,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // Conditional spread instead of `undefined` ternary —
  // `exactOptionalPropertyTypes` rejects the latter.
  ...(skipWebServer
    ? {}
    : {
        webServer: {
          // Use `next start` against a pre-built bundle when possible —
          // dev mode is fine locally but slower. CI calls this with the
          // build already cached from the lint/typecheck job.
          command: `pnpm next dev --port ${PORT}`,
          url: BASE_URL,
          reuseExistingServer: !process.env['CI'],
          timeout: 120_000,
          env: {
            NODE_ENV: 'development',
            SKIP_ENV_VALIDATION: '1',
          },
        },
      }),
});
