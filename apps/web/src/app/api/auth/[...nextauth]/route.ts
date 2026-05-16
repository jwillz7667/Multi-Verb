/**
 * Auth.js v5 — App Router route handlers.
 *
 * Re-exports the GET and POST handlers from the auth config. Auth.js
 * mounts every endpoint it needs (sign-in, sign-out, callback,
 * session, csrf, verify-request, ...) under this single catch-all
 * route.
 */

import { handlers } from '@/lib/auth';

export const { GET, POST } = handlers;

export const runtime = 'nodejs';
