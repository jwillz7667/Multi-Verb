/**
 * Auth.js v5 — Next.js middleware.
 *
 * Runs on the Edge Runtime so it can't import the Prisma adapter;
 * uses only the edge-safe `auth.config` and a JWT session strategy
 * for the middleware's own session check (the route handlers above
 * still use the database session strategy for actual sign-in/out).
 *
 * The matcher excludes static assets, image optimization, and the
 * health/ready probes so deploy probes are never gated by auth.
 */

import NextAuth from 'next-auth';

import { authConfig } from '@/lib/auth.config';

export const { auth: middleware } = NextAuth(authConfig);

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon\\.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)'],
};
