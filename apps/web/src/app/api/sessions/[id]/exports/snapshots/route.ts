/**
 * GET /api/sessions/[id]/exports/snapshots
 *
 * Returns every state snapshot for the session as JSONL. One row per
 * line, each row containing `ts`, `tick_id`, and the SessionState blob
 * exactly as the engine persisted it. This is the canonical replay
 * artifact — analysts can reconstruct the full SessionState arc and
 * cross-reference it with the decision log.
 *
 * Why we serve the full session in one response: the worst case
 * (90-min @ 2 Hz × ~5 KB/snapshot ≈ 50 MB) sits inside Vercel's
 * response buffering envelope. If we later raise the session-length
 * cap, swap the buffered build for a `ReadableStream` that yields one
 * line per snapshot — the formatter is already line-oriented so the
 * change is local.
 *
 * Auth: any authenticated user. Tenancy defers to the org_id rollout
 * (brief §10.3 / ADR-0002), matching transcript + decision exports.
 */

import { NextResponse } from 'next/server';

import { formatSnapshotsJsonl } from '@/features/exports';
import {
  findSessionForReplay,
  iterateAllSnapshotsForSession,
  type StateSnapshotRow,
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

  const snapshots: StateSnapshotRow[] = [];
  for await (const chunk of iterateAllSnapshotsForSession(sessionId)) {
    snapshots.push(...chunk);
  }

  const body = formatSnapshotsJsonl(snapshots);
  const filename = makeFilename(session.livekitRoomName, sessionId);

  return new NextResponse(body, {
    status: 200,
    headers: {
      // `application/x-ndjson` is the de-facto JSONL media type used by
      // jq, Elasticsearch bulk, and most line-oriented JSON tools.
      'content-type': 'application/x-ndjson; charset=utf-8',
      'content-disposition': `attachment; filename="${filename}"`,
      'cache-control': 'private, no-store',
    },
  });
}

function makeFilename(roomName: string, sessionId: string): string {
  const safeRoom = roomName.replace(/[^A-Za-z0-9._-]/g, '-').slice(0, 80);
  const shortId = sessionId.slice(0, 9);
  return `snapshots-${safeRoom}-${shortId}.jsonl`;
}
