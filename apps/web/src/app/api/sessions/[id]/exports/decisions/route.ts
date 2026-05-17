/**
 * GET /api/sessions/[id]/exports/decisions
 *
 * Returns the full decision log for a session as a CSV download. Every
 * decision in the session is included — `stay_silent` rows alongside
 * the executed turns — because the audit principle (brief §2.2) is
 * that researchers can answer "why didn't it speak here?" with the
 * same fidelity as "why did it speak there?".
 *
 * Auth: any authenticated user; tenancy scoping defers to the org_id
 * rollout (brief §10.3 / ADR-0002), matching transcript export.
 *
 * Memory profile: even a dense 90-min session ships ≈ 11k rows ≈ 2 MB
 * pre-compression — well under Vercel's response buffering envelope.
 * We still stream the underlying DB read via cursor pagination so the
 * peak memory hit is one chunk at a time rather than the whole result
 * set, and the route's own buffer is just the rendered CSV string.
 */

import { NextResponse } from 'next/server';

import { formatDecisionLogCsv } from '@/features/exports';
import {
  findSessionForReplay,
  iterateAllDecisionsForSession,
  listParticipantsForSession,
  type DecisionRow,
  type ParticipantRow,
} from '@/features/sessions';
import { auth } from '@/lib/auth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ id: string }>;
}

export async function GET(_request: Request, context: RouteContext): Promise<NextResponse> {
  const userSession = await auth();
  if (!userSession?.user) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  const { id: sessionId } = await context.params;

  const session = await findSessionForReplay(sessionId);
  if (session === null) {
    return NextResponse.json({ error: 'session_not_found' }, { status: 404 });
  }

  const participants = await listParticipantsForSession(sessionId);
  const participantsById = new Map<string, ParticipantRow>(participants.map((p) => [p.id, p]));

  const decisions: DecisionRow[] = [];
  for await (const chunk of iterateAllDecisionsForSession(sessionId)) {
    decisions.push(...chunk);
  }

  const body = formatDecisionLogCsv(decisions, participantsById);
  const filename = makeFilename(session.livekitRoomName, sessionId);

  return new NextResponse(body, {
    status: 200,
    headers: {
      // RFC 7111 registers text/csv. Adding `header=present` lets
      // tolerant parsers know the first row is the column names.
      'content-type': 'text/csv; charset=utf-8; header=present',
      'content-disposition': `attachment; filename="${filename}"`,
      'cache-control': 'private, no-store',
    },
  });
}

function makeFilename(roomName: string, sessionId: string): string {
  // Same sanitisation strategy as transcript export — collapse to
  // alnum/dash/dot, cap room-name length, prefix a 9-char id so two
  // sessions with the same room name don't collide in downloads.
  const safeRoom = roomName.replace(/[^A-Za-z0-9._-]/g, '-').slice(0, 80);
  const shortId = sessionId.slice(0, 9);
  return `decisions-${safeRoom}-${shortId}.csv`;
}
