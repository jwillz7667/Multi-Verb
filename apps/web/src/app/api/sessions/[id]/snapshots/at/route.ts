/**
 * GET /api/sessions/[id]/snapshots/at?ts=<ISO8601>
 *
 * Returns the freshest `state_snapshots` row at or before `ts` for the
 * replay state-snapshot panel. Companion to the page-bootstrap window
 * (`listSnapshotsByRange`) — when the researcher scrubs past that
 * 5-minute window, the panel calls this route for one row instead of
 * round-tripping a wide range.
 *
 * Why ≤ ts (not nearest): replays display a frozen state at the
 * scrubber position. The "what was true at this instant" semantic is
 * always the most recent prior tick — picking a future snapshot would
 * show a state the researcher hadn't witnessed yet at the scrubber
 * moment.
 *
 * Status codes:
 *   - 200 — snapshot found; body is the wire DTO
 *   - 400 — `ts` query param missing or unparseable as a date
 *   - 401 — unauthenticated
 *   - 404 — session unknown, or no snapshot ≤ ts (e.g., scrubber
 *           landed before the first tick was persisted)
 */

import { NextResponse } from 'next/server';

import { findSessionForReplay, findSnapshotAtOrBefore } from '@/features/sessions';
import { auth } from '@/lib/auth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ id: string }>;
}

interface SnapshotResponse {
  id: string;
  tick_id: number;
  ts: string;
  state: unknown;
}

export async function GET(request: Request, context: RouteContext): Promise<NextResponse> {
  const userSession = await auth();
  if (!userSession?.user) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  const { id: sessionId } = await context.params;
  const url = new URL(request.url);
  const tsParam = url.searchParams.get('ts');
  if (tsParam === null || tsParam === '') {
    return NextResponse.json({ error: 'ts_required' }, { status: 400 });
  }
  const tsMs = Date.parse(tsParam);
  if (Number.isNaN(tsMs)) {
    return NextResponse.json({ error: 'ts_invalid' }, { status: 400 });
  }

  const session = await findSessionForReplay(sessionId);
  if (session === null) {
    return NextResponse.json({ error: 'session_not_found' }, { status: 404 });
  }

  const snapshot = await findSnapshotAtOrBefore(sessionId, new Date(tsMs));
  if (snapshot === null) {
    return NextResponse.json({ error: 'no_snapshot_at_ts' }, { status: 404 });
  }

  const body: SnapshotResponse = {
    id: snapshot.id,
    // bigint → number: tick_id at 2 Hz can't approach Number.MAX_SAFE_INTEGER
    // within any realistic session duration.
    tick_id: Number(snapshot.tickId),
    ts: snapshot.ts.toISOString(),
    state: snapshot.state,
  };
  return NextResponse.json(body);
}
