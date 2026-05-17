/**
 * Lazy `WebhookReceiver` singleton + raw-body verification helper.
 *
 * The receiver wraps the HMAC-SHA256 verifier LiveKit Cloud signs
 * webhook payloads with. We cache one instance on `globalThis` (matches
 * the R2 client + Prisma client patterns in this package) so hot reload
 * doesn't leak verifiers per file change.
 *
 * Why split this off `webhook.ts`:
 *   - `webhook.ts` is the pure processor — no env access, no IO except
 *     through the injected repo. That keeps its unit tests trivial.
 *   - `webhook-receiver.ts` owns the messy edges: env reads, the
 *     livekit-server-sdk import, signature verification. Routes
 *     compose both.
 */

import 'server-only';

import { WebhookReceiver } from 'livekit-server-sdk';

import { serverEnv } from '@/lib/env';

import { LiveKitWebhookNotConfiguredError, WebhookSignatureError } from './errors';

import type { WebhookEvent } from 'livekit-server-sdk';

declare global {
  var __verbioLiveKitWebhookReceiver: WebhookReceiver | undefined;
}

interface WebhookReceiverConfig {
  apiKey: string;
  apiSecret: string;
}

function readConfig(): WebhookReceiverConfig {
  if (serverEnv === null) {
    throw new LiveKitWebhookNotConfiguredError(['<server context unavailable>']);
  }
  const { LIVEKIT_API_KEY, LIVEKIT_API_SECRET } = serverEnv;
  const missing: string[] = [];
  if (LIVEKIT_API_KEY === undefined) missing.push('LIVEKIT_API_KEY');
  if (LIVEKIT_API_SECRET === undefined) missing.push('LIVEKIT_API_SECRET');
  if (LIVEKIT_API_KEY === undefined || LIVEKIT_API_SECRET === undefined) {
    throw new LiveKitWebhookNotConfiguredError(missing);
  }
  return { apiKey: LIVEKIT_API_KEY, apiSecret: LIVEKIT_API_SECRET };
}

/**
 * Lazy receiver. Caller handles `LiveKitWebhookNotConfiguredError` at
 * the route boundary (→ 503 so LiveKit retries once ops fixes env).
 */
export function getWebhookReceiver(): WebhookReceiver {
  if (globalThis.__verbioLiveKitWebhookReceiver === undefined) {
    const config = readConfig();
    globalThis.__verbioLiveKitWebhookReceiver = new WebhookReceiver(
      config.apiKey,
      config.apiSecret,
    );
  }
  return globalThis.__verbioLiveKitWebhookReceiver;
}

/**
 * Verify the raw body + `Authorization` header and return the parsed
 * `WebhookEvent`. Throws `WebhookSignatureError` on any verifier
 * failure (bad sig, expired claim, malformed JWT, missing header).
 *
 * `rawBody` MUST be the request body as a string — verification
 * recomputes the HMAC over the exact bytes the server received, so a
 * JSON.parse + JSON.stringify round-trip would silently break it.
 */
export async function verifyWebhookEvent(
  rawBody: string,
  authHeader: string | null,
): Promise<WebhookEvent> {
  if (authHeader === null || authHeader === '') {
    throw new WebhookSignatureError('missing_authorization_header');
  }
  const receiver = getWebhookReceiver();
  try {
    return await receiver.receive(rawBody, authHeader);
  } catch (err) {
    const reason = err instanceof Error ? err.message : 'verifier_error';
    throw new WebhookSignatureError(reason);
  }
}

/** Test-only: reset the lazy receiver so a fresh env can be read. */
export function __resetWebhookReceiverForTests(): void {
  globalThis.__verbioLiveKitWebhookReceiver = undefined;
}
