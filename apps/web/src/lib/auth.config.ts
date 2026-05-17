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

// `dev:auto-sign-in` — set DEV_AUTO_SIGN_IN_USER to a user id at boot
// to bypass Auth.js entirely in local dev. Lets contributors drive the
// UI without setting up a Resend key. Gated by NODE_ENV !== production
// so it can never authorize a real deployment, even if the env var
// leaks into Vercel by mistake.
const isProd = process.env.NODE_ENV === 'production';
const devAutoSignInUser = isProd ? undefined : process.env['DEV_AUTO_SIGN_IN_USER'];

export const isDevAutoSignInEnabled = devAutoSignInUser !== undefined && devAutoSignInUser !== '';

export const authConfig = {
  pages: {
    signIn: '/sign-in',
    verifyRequest: '/sign-in/check-email',
    error: '/sign-in/error',
  },
  callbacks: {
    authorized({ auth, request }) {
      if (isDevAutoSignInEnabled) return true;

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
