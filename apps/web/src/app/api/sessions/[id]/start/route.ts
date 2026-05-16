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

interface RouteContext {
  params: Promise<{ id: string }>;
}

export async function POST(_request: Request, context: RouteContext): Promise<NextResponse> {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  const { id } = await context.params;
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
