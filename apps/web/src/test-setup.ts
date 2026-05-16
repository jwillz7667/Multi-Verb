import '@testing-library/jest-dom/vitest';

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
