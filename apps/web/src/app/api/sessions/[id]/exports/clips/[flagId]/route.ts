/**
 * GET /api/sessions/[id]/exports/clips/[flagId]
 *
 * Returns an .mp3 audio clip centered on the flag's `ts`, cut from the
 * session's composite LiveKit recording stored in R2 (brief §11.2,
 * §13.3). Researchers drop `flag_moment` bookmarks mid-session; the
 * replay UI exposes one downloadable clip per flag so they can attach
 * snippets to a report without sharing the full recording.
 *
 * Pipeline:
 *   1. auth → 401 if anonymous,
 *   2. flag lookup; reject if the flag doesn't belong to this session
 *      so a crafted `/clips/{otherSessionsFlagId}` can't bleed across
 *      session boundaries (the same defence the SSE backfill applies
 *      for utterance / decision / snapshot cursors),
 *   3. session lookup; 422 if the session hasn't started yet or the
 *      composite recording hasn't been written by the egress webhook,
 *   4. mint a short-TTL signed R2 GET URL — ffmpeg uses HTTP Range
 *      against that URL to fast-seek to the clip window (see
 *      audio-clip module docstring for the `-ss` ordering rationale),
 *   5. spawn ffmpeg, stream stdout straight into the Response body.
 *
 * Auth: any authenticated user. Tenancy will gate further once `org_id`
 * lands (brief §10.3 / ADR-0002).
 *
 * Status codes:
 *   - 200 — clip streaming as `audio/mpeg` with attachment disposition,
 *   - 401 — unauthenticated,
 *   - 404 — session or flag unknown, or the flag belongs to a different
 *           session (deliberately indistinguishable from "not found" to
 *           avoid enumeration),
 *   - 422 — session hasn't started yet, or no composite recording,
 *   - 503 — R2 not configured (env vars missing in this deployment).
 */

import { NextResponse } from 'next/server';

import { computeClipWindow, makeClipFilename, spawnFfmpegClipStream } from '@/features/exports';
import { R2NotConfiguredError, signGetUrl } from '@/features/recordings';
import { findSessionFlagById, findSessionForReplay } from '@/features/sessions';
import { auth } from '@/lib/auth';
import { orgIdForUser } from '@/lib/identity';
import { scopedDb } from '@/lib/scoped-db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ id: string; flagId: string }>;
}

// Match the audio player's URL TTL — 1 hour is well past the few-second
// ffmpeg lifetime but short enough that a leaked URL is rapidly useless.
const R2_INPUT_URL_TTL_SECONDS = 60 * 60;

export async function GET(
  _request: Request,
  context: RouteContext,
): Promise<NextResponse | Response> {
  const userSession = await auth();
  if (!userSession?.user?.id) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  const { id: sessionId, flagId } = await context.params;

  // Tenancy gate first: a cross-org flag id must not be readable, even
  // briefly, before the session ownership check. Without this gate the
  // unscoped `findSessionFlagById` would return another tenant's flag
  // row (we'd still 404, but the read itself is an info-leak vector).
  const orgId = orgIdForUser(userSession.user.id);
  const owned = await scopedDb(orgId).sessions.findById(sessionId);
  if (owned === null) {
    return NextResponse.json({ error: 'session_not_found' }, { status: 404 });
  }

  const flag = await findSessionFlagById(flagId);
  // Cross-session smuggling guard: a flag id from another session must
  // 404, not return that session's audio. Tested explicitly.
  if (flag?.sessionId !== sessionId) {
    return NextResponse.json({ error: 'flag_not_found' }, { status: 404 });
  }

  const session = await findSessionForReplay(sessionId);
  if (session === null) {
    return NextResponse.json({ error: 'session_not_found' }, { status: 404 });
  }

  if (session.actualStart === null) {
    // No `actual_start` means the session never began (scheduled but
    // cancelled, or stuck pre-roll). There's nothing to clip relative to.
    return NextResponse.json({ error: 'session_not_started' }, { status: 422 });
  }

  if (session.recordingUrl === null || session.recordingUrl === '') {
    // Composite recording hasn't landed yet — egress writes async after
    // session end. The UI button is disabled when this is the case, but
    // we still answer crisply for direct-link probes.
    return NextResponse.json({ error: 'recording_not_ready' }, { status: 422 });
  }

  let signedInputUrl: string;
  try {
    signedInputUrl = await signGetUrl(session.recordingUrl, R2_INPUT_URL_TTL_SECONDS);
  } catch (err) {
    if (err instanceof R2NotConfiguredError) {
      return NextResponse.json({ error: 'r2_not_configured' }, { status: 503 });
    }
    throw err;
  }

  const window = computeClipWindow(flag.ts, session.actualStart);
  const { body, done, stderr } = spawnFfmpegClipStream({
    inputUrl: signedInputUrl,
    window,
  });

  // Fire-and-forget logging of ffmpeg's exit; the response is already
  // streaming by the time this resolves. A non-zero exit means the
  // browser saw a truncated/empty mp3 — surface in server logs so the
  // operator can tell the difference between an empty clip and a real
  // failure.
  void done.then(async (code) => {
    if (code !== 0) {
      const stderrTail = await stderr;
      console.error(
        `[clip-export] ffmpeg exit=${String(code)} session=${sessionId} flag=${flagId} stderr=${stderrTail}`,
      );
    }
  });

  const filename = makeClipFilename(session.livekitRoomName, flag.id, flag.ts);

  return new Response(body, {
    status: 200,
    headers: {
      'content-type': 'audio/mpeg',
      'content-disposition': `attachment; filename="${filename}"`,
      'cache-control': 'private, no-store',
    },
  });
}
