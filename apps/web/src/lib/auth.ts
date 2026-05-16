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
import NextAuth, { type NextAuthResult } from 'next-auth';
import Resend from 'next-auth/providers/resend';

import { authConfig } from './auth.config';
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

export const { handlers, auth, signIn, signOut }: NextAuthResult = NextAuth({
  ...authConfig,
  adapter: PrismaAdapter(db),
  session: { strategy: 'database' },
  trustHost: true,
  secret: serverEnv.AUTH_SECRET,
  providers: [...authConfig.providers, ...resendProvider],
});
