/**
 * Integration vitest setup.
 *
 * Mirrors `src/test-setup.ts`'s `server-only` stub so importing
 * production modules (db, scoped-db, etc.) doesn't crash on the
 * "Client Component module" guard. Everything else those tests
 * environment-shim — jsdom, env defaults, jest-dom matchers — is
 * intentionally absent here: integration tests run in node against a
 * real Postgres and do not assert on DOM shapes.
 */

import { vi } from 'vitest';

vi.mock('server-only', () => ({}));
