/**
 * GET /api/sessions/[id]/exports/transcript?format=txt|vtt
 *
 * Returns the session transcript as a downloadable file. The
 * transcript merges participant utterances + executed moderator turns
 * (the only decisions that produced audio) and renders them as either
 * a human-readable plaintext stream or a WebVTT cue file (brief
 * §11.2's export panel).
 *
 * Why a single endpoint with a `format` query param instead of two:
 * the underlying source of truth (utterances + executed decisions) is
 * identical — the only difference is the serialiser. Splitting the
 * route would force the auth + lookup + cursor iteration to be
 * duplicated.
 *
 * Streaming vs. buffered: for a 60-min session a transcript runs
 * ~50–200 KB. That's well under Vercel's response buffering limit and
 * far below memory pressure, so we build the string in memory and
 * return it. If sessions grow past ~5 MB transcripts we should switch
 * to a `ReadableStream` to avoid holding the full payload — left as
 * a follow-up since v1 targets bounded session length.
 *
 * Auth: any authenticated user. Tenancy scoping defers to the org_id
 * rollout (brief §10.3 / ADR-0002), matching the rest of the replay
 * surface area.
 */

import { NextResponse } from 'next/server';

import {
  buildTranscriptLines,
  formatTranscriptTxt,
  formatTranscriptVtt,
  type TranscriptHeader,
} from '@/features/exports';
import {
  findSessionForReplay,
  iterateAllUtterancesForSession,
  listExecutedModeratorTurns,
  listParticipantsForSession,
  type UtteranceWithSpeakerRow,
} from '@/features/sessions';
import { auth } from '@/lib/auth';
import { orgIdForUser } from '@/lib/identity';
import { scopedDb } from '@/lib/scoped-db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ id: string }>;
}

type ExportFormat = 'txt' | 'vtt';

export async function GET(request: Request, context: RouteContext): Promise<NextResponse> {
  const userSession = await auth();
  if (!userSession?.user?.id) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  const { id: sessionId } = await context.params;

  const url = new URL(request.url);
  const rawFormat = (url.searchParams.get('format') ?? 'txt').toLowerCase();
  const format = parseFormat(rawFormat);
  if (format === null) {
    return NextResponse.json(
      { error: 'invalid_format', detail: 'format must be "txt" or "vtt"' },
      { status: 400 },
    );
  }

  // Tenancy gate: scopedDb confirms ownership before any per-session
  // data is loaded. Cross-org access returns 404, indistinguishable
  // from a missing id so the response can't fingerprint other tenants.
  const orgId = orgIdForUser(userSession.user.id);
  const owned = await scopedDb(orgId).sessions.findById(sessionId);
  if (owned === null) {
    return NextResponse.json({ error: 'session_not_found' }, { status: 404 });
  }

  const session = await findSessionForReplay(sessionId);
  if (session === null) {
    return NextResponse.json({ error: 'session_not_found' }, { status: 404 });
  }

  const [participants, moderatorTurns] = await Promise.all([
    listParticipantsForSession(sessionId),
    listExecutedModeratorTurns(sessionId),
  ]);

  const utterances: UtteranceWithSpeakerRow[] = [];
  for await (const chunk of iterateAllUtterancesForSession(sessionId)) {
    utterances.push(...chunk);
  }

  // Anchor exports at `actualStart` so cues read like "this many
  // seconds into the recording". If the session never went live (rare
  // — researcher created it then ended it), fall back to `createdAt`
  // so the cue clock still increments monotonically from 0 instead of
  // showing a wall-clock offset that means nothing.
  const anchor = session.actualStart ?? session.createdAt;

  const lines = buildTranscriptLines({
    anchor,
    utterances,
    moderatorDecisions: moderatorTurns,
  });

  const header: TranscriptHeader = {
    sessionId,
    livekitRoomName: session.livekitRoomName,
    actualStart: session.actualStart,
    actualEnd: session.actualEnd,
    participantNames: participants
      .filter((p) => p.role !== 'moderator')
      .map((p) => p.displayName)
      .sort(),
  };

  const body =
    format === 'txt' ? formatTranscriptTxt(header, lines) : formatTranscriptVtt(header, lines);

  const filename = makeFilename(session.livekitRoomName, sessionId, format);
  return new NextResponse(body, {
    status: 200,
    headers: {
      // text/plain for .txt; text/vtt is the registered media type for
      // WebVTT cues per the W3C VTT spec.
      'content-type': format === 'txt' ? 'text/plain; charset=utf-8' : 'text/vtt; charset=utf-8',
      'content-disposition': `attachment; filename="${filename}"`,
      // Per-export bytes are derived from never-mutated rows for a
      // fixed session; an HTTP cache wouldn't help (request is
      // authenticated + short-lived) but we still mark it private so
      // a shared proxy never holds it.
      'cache-control': 'private, no-store',
    },
  });
}

function parseFormat(raw: string): ExportFormat | null {
  if (raw === 'txt') return 'txt';
  if (raw === 'vtt') return 'vtt';
  return null;
}

function makeFilename(roomName: string, sessionId: string, format: ExportFormat): string {
  // Quote-safe + filesystem-safe filename: collapse anything not
  // alnum/dash/dot to `-`, and prefix with a short id so two sessions
  // with identical room names don't overwrite each other in a
  // researcher's downloads folder.
  const safeRoom = roomName.replace(/[^A-Za-z0-9._-]/g, '-').slice(0, 80);
  // 9 chars covers a UUID/CUID's leading random block well enough to
  // disambiguate two sessions in the same researcher's downloads
  // folder while keeping the filename readable.
  const shortId = sessionId.slice(0, 9);
  return `transcript-${safeRoom}-${shortId}.${format}`;
}
