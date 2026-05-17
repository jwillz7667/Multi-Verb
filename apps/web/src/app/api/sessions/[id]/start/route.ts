/**
 * POST /api/sessions/[id]/start — dispatch the moderator agent.
 *
 * Sets the room expecting the engine to attach. The engine's
 * `on_room_connected` is what authoritatively flips the session row
 * to `status="live"`; this endpoint only triggers dispatch.
 */

import { NextResponse } from 'next/server';

import { SessionAlreadyEndedError, SessionNotFoundError, startSession } from '@/features/sessions';
import { auth } from '@/lib/auth';
import { orgIdForUser } from '@/lib/identity';
import { scopedDb } from '@/lib/scoped-db';

interface RouteContext {
  params: Promise<{ id: string }>;
}

export async function POST(_request: Request, context: RouteContext): Promise<NextResponse> {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  const { id } = await context.params;

  // Tenancy gate: cross-org callers must 404 before LiveKit dispatch.
  // Without this gate a researcher in org A could POST this route with a
  // guessed session id from org B and (a) kick off agent dispatch into
  // another tenant's live room and (b) read the session row back in the
  // response body. Both are silent cross-tenant breaches.
  const orgId = orgIdForUser(session.user.id);
  const owned = await scopedDb(orgId).sessions.findById(id);
  if (owned === null) {
    return NextResponse.json({ error: 'session_not_found' }, { status: 404 });
  }

  try {
    const row = await startSession(id);
    return NextResponse.json({ ok: true, session: row });
  } catch (err) {
    if (err instanceof SessionNotFoundError) {
      return NextResponse.json({ error: err.code }, { status: 404 });
    }
    if (err instanceof SessionAlreadyEndedError) {
      return NextResponse.json({ error: err.code }, { status: 409 });
    }
    throw err;
  }
}
