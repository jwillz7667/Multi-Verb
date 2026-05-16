/**
 * Playwright globalSetup — seed an Auth.js session before the suite.
 *
 * The dashboard is fully auth-gated; in production the only way in
 * is a Resend magic link. For E2E we side-step the email round-trip
 * by writing an `auth_sessions` row directly via Prisma and saving
 * the session cookie to disk as Playwright's storage state. Tests
 * then inherit that signed-in state via `storageState` in the config.
 *
 * This module is loaded only when E2E is actually running, so the
 * top-level `server-only` imports it pulls in via `@/lib/db` are
 * safe (Node test runtime, never the browser).
 */

import { randomBytes } from 'node:crypto';
import { mkdirSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

import { db } from './db';

import type { FullConfig } from '@playwright/test';

const E2E_USER_EMAIL = 'e2e@verbio.test';
const E2E_USER_NAME = 'E2E Researcher';

export const STORAGE_STATE = resolve(import.meta.dirname, '.auth/session.json');

async function ensureSchemaApplied(): Promise<void> {
  // The E2E expects Alembic to have run against the dev Postgres so
  // the moderated_sessions / participants / utterances tables exist.
  // We surface a clear error if that hasn't happened rather than
  // letting a Prisma "table does not exist" stack trace fly past.
  try {
    await db.moderatedSession.count();
  } catch (err) {
    throw new Error(
      'Postgres schema is not up to date. Run `cd services/engine && uv run alembic upgrade head` ' +
        'against the dev database before running the E2E suite.\n' +
        `Underlying error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

export default async function globalSetup(_config: FullConfig): Promise<void> {
  await ensureSchemaApplied();

  // Idempotent upsert: a hung previous run may have left the user.
  const user = await db.user.upsert({
    where: { email: E2E_USER_EMAIL },
    update: { name: E2E_USER_NAME, emailVerified: new Date() },
    create: { email: E2E_USER_EMAIL, name: E2E_USER_NAME, emailVerified: new Date() },
  });

  // Mint a fresh session that lives long enough for the suite.
  const sessionToken = `e2e-${randomBytes(32).toString('hex')}`;
  const expires = new Date(Date.now() + 6 * 60 * 60 * 1000);
  await db.session.deleteMany({ where: { userId: user.id } });
  await db.session.create({
    data: {
      sessionToken,
      userId: user.id,
      expires,
    },
  });

  const cookieName =
    process.env['AUTH_URL']?.startsWith('https://') === true
      ? '__Secure-authjs.session-token'
      : 'authjs.session-token';

  const url = new URL(process.env['PLAYWRIGHT_BASE_URL'] ?? 'http://127.0.0.1:3100');

  const storageState = {
    cookies: [
      {
        name: cookieName,
        value: sessionToken,
        domain: url.hostname,
        path: '/',
        expires: Math.floor(expires.getTime() / 1000),
        httpOnly: true,
        secure: url.protocol === 'https:',
        sameSite: 'Lax' as const,
      },
    ],
    origins: [],
  };

  mkdirSync(dirname(STORAGE_STATE), { recursive: true });
  // Write atomically — Playwright opens the file from worker
  // processes the moment globalSetup returns.
  const tmp = `${STORAGE_STATE}.tmp`;
  writeFileSync(tmp, JSON.stringify(storageState, null, 2), 'utf-8');
  renameSync(tmp, STORAGE_STATE);
}
