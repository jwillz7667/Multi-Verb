/**
 * Auth.js v5 — edge-safe configuration.
 *
 * Split out from `auth.ts` because Next.js middleware runs on the
 * Edge Runtime, which can't import the Prisma adapter (Node-only).
 * This file holds the bits the middleware needs: providers list,
 * pages, callbacks that don't touch the database.
 *
 * The full config in `auth.ts` extends this with the Prisma adapter
 * and Resend magic-link sender.
 */

import type { NextAuthConfig } from 'next-auth';

export const authConfig = {
  pages: {
    signIn: '/sign-in',
    verifyRequest: '/sign-in/check-email',
    error: '/sign-in/error',
  },
  callbacks: {
    authorized({ auth, request }) {
      const isLoggedIn = auth?.user !== undefined;
      const path = request.nextUrl.pathname;

      const publicRoutes = ['/sign-in', '/api/health', '/api/ready'];
      const isPublic = publicRoutes.some((r) => path === r || path.startsWith(`${r}/`));
      const isAuthApi = path.startsWith('/api/auth/');

      if (isPublic || isAuthApi) return true;
      return isLoggedIn;
    },
  },
  providers: [],
} satisfies NextAuthConfig;
