/**
 * Auth.js v5 — full server-side configuration.
 *
 * Extends `auth.config.ts` (edge-safe) with the Prisma adapter and
 * Resend magic-link provider. Imported only by route handlers and
 * server components — never by middleware (Edge Runtime can't load
 * the Prisma client).
 *
 * Exports:
 *   - `handlers`  : the API route handlers (GET/POST for App Router)
 *   - `auth`      : the helper used in Server Components/Actions to
 *                    read the current session
 *   - `signIn`    : programmatic sign-in
 *   - `signOut`   : programmatic sign-out
 *
 * The Resend provider is only attached when AUTH_RESEND_KEY +
 * AUTH_EMAIL_FROM are set; locally you can run without email by using
 * a stub provider injected at test time.
 */

import 'server-only';

import { PrismaAdapter } from '@auth/prisma-adapter';
import NextAuth, { type NextAuthResult, type Session } from 'next-auth';
import Resend from 'next-auth/providers/resend';

import { authConfig, isDevAutoSignInEnabled } from './auth.config';
import { db } from './db';
import { serverEnv } from './env';

if (serverEnv === null) {
  throw new Error('auth.ts must only be imported on the server');
}

const resendProvider = serverEnv.AUTH_RESEND_KEY
  ? [
      Resend({
        apiKey: serverEnv.AUTH_RESEND_KEY,
        from: serverEnv.AUTH_EMAIL_FROM ?? 'no-reply@verbio.local',
      }),
    ]
  : [];

const nextAuth: NextAuthResult = NextAuth({
  ...authConfig,
  adapter: PrismaAdapter(db),
  session: { strategy: 'database' },
  trustHost: true,
  secret: serverEnv.AUTH_SECRET,
  providers: [...authConfig.providers, ...resendProvider],
});

export const { handlers, signIn, signOut } = nextAuth;

// `dev:auto-sign-in` — when DEV_AUTO_SIGN_IN_USER is set (dev only,
// see auth.config.ts), `auth()` returns a synthetic Session carrying
// the configured user id. This lets the UI render without a Resend-
// backed sign-in flow. The same user id is what the dev contributor
// will see attributed in audit logs, so use something recognisable.
const devUserId = process.env['DEV_AUTO_SIGN_IN_USER'];
const devUserEmail = process.env['DEV_AUTO_SIGN_IN_EMAIL'] ?? 'dev@local.verbio';

async function devAuth(): Promise<Session | null> {
  if (devUserId === undefined || devUserId === '') return null;
  // Ensure the User row exists so downstream foreign keys (sessions,
  // researcher_actions) don't trip. Upsert by id, idempotent.
  await db.user.upsert({
    where: { id: devUserId },
    update: {},
    create: { id: devUserId, email: devUserEmail, name: 'Dev User' },
  });
  return {
    user: { id: devUserId, email: devUserEmail, name: 'Dev User' },
    expires: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
  };
}

export const auth: NextAuthResult['auth'] = isDevAutoSignInEnabled
  ? ((async () => devAuth()) as NextAuthResult['auth'])
  : nextAuth.auth;
