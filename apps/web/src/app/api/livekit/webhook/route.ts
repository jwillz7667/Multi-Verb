/**
 * POST /api/livekit/webhook — LiveKit egress webhook receiver.
 *
 * LiveKit Cloud POSTs webhook envelopes here when an egress changes
 * state (started / updated / ended) and for other room/participant
 * lifecycle events we currently ignore. The body is a JWT-signed
 * payload — verification (HMAC-SHA256 over the raw bytes with the
 * shared `LIVEKIT_API_SECRET`) is what authorises the caller; there is
 * no Auth.js user context for these requests.
 *
 * Status code map (verbio-side semantics — see `webhook.ts` for the
 * rationale on returning 200 for ignored / failed-egress / unknown
 * outcomes):
 *
 *   - 200 OK   — signature valid, processing finished (any branch)
 *   - 401      — signature verification failed (bad secret, replay)
 *   - 503      — LiveKit creds missing; LiveKit will retry
 *   - 500      — unexpected error inside the processor
 *
 * Node runtime: the livekit-server-sdk verifier uses Node `crypto`,
 * and the Prisma client we update with isn't compatible with Vercel
 * Edge.
 */

import { NextResponse } from 'next/server';

import {
  defaultWebhookRecordingsRepo,
  LiveKitWebhookNotConfiguredError,
  processEgressWebhookEvent,
  verifyWebhookEvent,
  WebhookSignatureError,
} from '@/features/recordings';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(request: Request): Promise<NextResponse> {
  // Raw body — verification recomputes HMAC over the exact bytes the
  // server received. A `request.json()` round-trip would silently
  // change whitespace / key ordering and break the signature.
  const rawBody = await request.text();
  const authHeader = request.headers.get('authorization') ?? request.headers.get('Authorization');

  let event;
  try {
    event = await verifyWebhookEvent(rawBody, authHeader);
  } catch (err) {
    if (err instanceof LiveKitWebhookNotConfiguredError) {
      console.error('livekit_webhook.creds_missing', { missing: err.missing });
      return NextResponse.json({ error: 'webhook_not_configured' }, { status: 503 });
    }
    if (err instanceof WebhookSignatureError) {
      console.warn('livekit_webhook.signature_failed', { reason: err.reason });
      return NextResponse.json({ error: 'invalid_signature' }, { status: 401 });
    }
    throw err;
  }

  try {
    const result = await processEgressWebhookEvent(event, {
      repo: defaultWebhookRecordingsRepo,
    });
    return NextResponse.json({ ok: true, result });
  } catch (err) {
    // Don't 5xx LiveKit for transient DB errors quietly — log loud,
    // then return 500 so the webhook delivery retries with backoff.
    console.error('livekit_webhook.processor_error', {
      event: event.event,
      egressId: event.egressInfo?.egressId,
      error: err instanceof Error ? err.message : String(err),
    });
    return NextResponse.json({ error: 'processor_error' }, { status: 500 });
  }
}
