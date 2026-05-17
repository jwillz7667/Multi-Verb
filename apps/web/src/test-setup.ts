import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';

// `server-only` throws on import outside an RSC build to enforce the
// server/client boundary. Vitest runs everything in the same JS context,
// so any test that touches a feature barrel containing `import
// 'server-only'` would crash at import time. Stub it to a no-op
// module-wide — boundary tests still mock the underlying services, so
// the real server-only guarantee isn't weakened by this shim.
vi.mock('server-only', () => ({}));

// Provide a minimal valid env before any module that imports `./lib/env`
// triggers its boot-time validation. Test files that exercise env parsing
// directly call `parseServerEnv` / `parseClientEnv` with their own inputs;
// these defaults only exist so the eager validation in env.ts doesn't
// throw during module init.
process.env['NEXT_PUBLIC_APP_URL'] ??= 'http://localhost:3000';
process.env['NEXT_PUBLIC_APP_NAME'] ??= 'Verbio';
process.env['DATABASE_URL_POOLED'] ??=
  'postgresql://verbio:verbio@localhost:6543/verbio?pgbouncer=true';
process.env['REDIS_URL'] ??= 'redis://localhost:6379';
process.env['AUTH_SECRET'] ??= 'test-secret-not-used-for-anything-real';
process.env['ENGINE_BASE_URL'] ??= 'http://localhost:8000';
process.env['ENGINE_ADMIN_TOKEN'] ??= 'test-admin-token';
