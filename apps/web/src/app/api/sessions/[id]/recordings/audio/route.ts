/**
 * GET /api/sessions/[id]/recordings/audio
 *
 * Mints a short-lived signed R2 URL for replay audio playback.
 *
 *   - default            → composite recording (`sessions.recording_url`)
 *   - `?participant=<identity>` → per-participant track from the
 *     `per_participant_recording_urls` JSON map
 *
 * Why a dedicated route instead of embedding signed URLs in the page
 * payload: signed URLs expire (default 1h here, hard cap 7d in
 * `signGetUrl`). Embedding them would tie the page's freshness to the
 * URL's TTL — a researcher who left the tab open past the TTL would
 * see broken audio with no path to recover except a hard refresh.
 * Routing through this endpoint means the audio player re-fetches on
 * play / on TTL expiry, and a stale page never serves a dead URL.
 *
 * Auth: any authenticated user. Tenancy scoping per session is
 * deferred until the org_id rollout (brief §10.3 / ADR-0002) — for now
 * the session id alone is the access boundary.
 *
 * Response shape:
 *   { url: string, expires_at: ISO8601, kind: 'composite' | 'participant',
 *     participant_identity?: string }
 *
 * Status codes:
 *   - 200 — signed URL minted
 *   - 401 — unauthenticated
 *   - 404 — session unknown, OR the requested track has no recording
 *   - 503 — R2 not configured (LiveKit egress wrote keys we can't sign)
 */

import { NextResponse } from 'next/server';

import {
  parseParticipantRecordingUrls,
  R2NotConfiguredError,
  signGetUrl,
} from '@/features/recordings';
import { findSessionForReplay } from '@/features/sessions';
import { auth } from '@/lib/auth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ id: string }>;
}

interface AudioUrlResponse {
  url: string;
  expires_at: string;
  kind: 'composite' | 'participant';
  participant_identity?: string;
}

const URL_TTL_SECONDS = 60 * 60; // 1 hour — matches signGetUrl default.

export async function GET(request: Request, context: RouteContext): Promise<NextResponse> {
  const userSession = await auth();
  if (!userSession?.user) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  const { id: sessionId } = await context.params;
  const session = await findSessionForReplay(sessionId);
  if (session === null) {
    return NextResponse.json({ error: 'session_not_found' }, { status: 404 });
  }

  // The query string is parsed via the URL constructor instead of
  // Next's `searchParams` helper — the helper is only available on
  // server components, not raw route handlers.
  const url = new URL(request.url);
  const participantIdentity = url.searchParams.get('participant');

  let r2Key: string | null;
  let kind: 'composite' | 'participant';
  if (participantIdentity !== null && participantIdentity !== '') {
    const perParticipant = parseParticipantRecordingUrls(session.perParticipantRecordingUrls);
    r2Key = perParticipant[participantIdentity] ?? null;
    kind = 'participant';
  } else {
    r2Key = session.recordingUrl;
    kind = 'composite';
  }

  if (r2Key === null) {
    // Same 404 shape whether the session has no composite yet, the
    // participant identity was wrong, or that identity just has no
    // per-track recording — the audio player surfaces "no recording"
    // either way, and we never differentiate to a caller poking the
    // route to enumerate which participants have audio.
    return NextResponse.json({ error: 'recording_not_found' }, { status: 404 });
  }

  let signedUrl: string;
  try {
    signedUrl = await signGetUrl(r2Key, URL_TTL_SECONDS);
  } catch (err) {
    if (err instanceof R2NotConfiguredError) {
      return NextResponse.json({ error: 'r2_not_configured' }, { status: 503 });
    }
    throw err;
  }

  // expires_at is a wall-clock hint for the audio player's TTL refresh
  // logic — it's the request-time `now + TTL`, not a server-clock
  // guarantee from R2 itself. Treat as advisory; the real authority is
  // the URL's embedded signature expiry.
  const expiresAt = new Date(Date.now() + URL_TTL_SECONDS * 1000).toISOString();
  const body: AudioUrlResponse = {
    url: signedUrl,
    expires_at: expiresAt,
    kind,
    ...(kind === 'participant' && participantIdentity !== null
      ? { participant_identity: participantIdentity }
      : {}),
  };
  return NextResponse.json(body);
}
