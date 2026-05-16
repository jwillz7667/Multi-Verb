import { describe, expect, it } from 'vitest';

import { parseClientEnv, parseServerEnv } from './env.js';

const completeServerEnv = {
  NODE_ENV: 'development',
  NEXT_PUBLIC_APP_URL: 'http://localhost:3000',
  NEXT_PUBLIC_APP_NAME: 'Verbio',
  DATABASE_URL_POOLED: 'postgresql://verbio:pw@localhost:6543/verbio?pgbouncer=true',
  REDIS_URL: 'rediss://default:pw@localhost:6379',
  AUTH_SECRET: 'rand-base64-32-chars-of-noise-here-okx',
  ENGINE_BASE_URL: 'http://localhost:8000',
  ENGINE_ADMIN_TOKEN: 'shared-with-engine-rand-base64',
} satisfies NodeJS.ProcessEnv;

describe('parseServerEnv', () => {
  it('parses a complete env with defaults applied', () => {
    const result = parseServerEnv(completeServerEnv);

    expect(result.isValid).toBe(true);
    if (!result.isValid) return;
    expect(result.data.NODE_ENV).toBe('development');
    expect(result.data.NEXT_PUBLIC_APP_NAME).toBe('Verbio');
    expect(result.data.REDIS_NAMESPACE).toBe('verbio:local');
    expect(result.data.AUTH_RESEND_KEY).toBeUndefined();
  });

  it('rejects missing required secrets', () => {
    const { AUTH_SECRET: _omitted, ...without } = completeServerEnv;

    const result = parseServerEnv(without);

    expect(result.isValid).toBe(false);
    if (result.isValid) return;
    const paths = result.error.issues.map((i) => i.path.join('.'));
    expect(paths).toContain('AUTH_SECRET');
  });

  it('rejects malformed URLs', () => {
    const result = parseServerEnv({
      ...completeServerEnv,
      DATABASE_URL_POOLED: 'not-a-url',
    });

    expect(result.isValid).toBe(false);
    if (result.isValid) return;
    expect(result.error.issues.some((i) => i.path[0] === 'DATABASE_URL_POOLED')).toBe(true);
  });

  it('rejects invalid NODE_ENV values', () => {
    const result = parseServerEnv({ ...completeServerEnv, NODE_ENV: 'staging' });

    expect(result.isValid).toBe(false);
    if (result.isValid) return;
    expect(result.error.issues.some((i) => i.path[0] === 'NODE_ENV')).toBe(true);
  });

  it('accepts optional R2 config when complete', () => {
    const result = parseServerEnv({
      ...completeServerEnv,
      R2_ACCOUNT_ID: 'abc',
      R2_ACCESS_KEY_ID: 'key',
      R2_SECRET_ACCESS_KEY: 'secret',
      R2_BUCKET: 'verbio-recordings',
      R2_PUBLIC_BASE_URL: 'https://cdn.verbio.app',
    });

    expect(result.isValid).toBe(true);
    if (!result.isValid) return;
    expect(result.data.R2_BUCKET).toBe('verbio-recordings');
  });
});

describe('parseClientEnv', () => {
  it('parses NEXT_PUBLIC_* vars only', () => {
    const result = parseClientEnv({
      NEXT_PUBLIC_APP_URL: 'https://app.verbio.app',
      AUTH_SECRET: 'should-not-leak',
    });

    expect(result.isValid).toBe(true);
    if (!result.isValid) return;
    expect(result.data.NEXT_PUBLIC_APP_URL).toBe('https://app.verbio.app');
    expect(Object.keys(result.data)).not.toContain('AUTH_SECRET');
  });

  it('rejects missing NEXT_PUBLIC_APP_URL', () => {
    const result = parseClientEnv({});

    expect(result.isValid).toBe(false);
    if (result.isValid) return;
    expect(result.error.issues.some((i) => i.path[0] === 'NEXT_PUBLIC_APP_URL')).toBe(true);
  });

  it('applies NEXT_PUBLIC_APP_NAME default', () => {
    const result = parseClientEnv({ NEXT_PUBLIC_APP_URL: 'http://localhost:3000' });

    expect(result.isValid).toBe(true);
    if (!result.isValid) return;
    expect(result.data.NEXT_PUBLIC_APP_NAME).toBe('Verbio');
  });
});
