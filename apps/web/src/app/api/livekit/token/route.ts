/**
 * POST /api/livekit/token — mint a participant token.
 *
 * Access policy:
 *   - `researcher` and `moderator` roles require an authenticated user
 *     AND the session must belong to the caller's org (P7 L2). Without
 *     the org gate a researcher in org A could mint a moderator token
 *     into another tenant's live room.
 *   - `participant` role is open to anyone who knows the session id
 *     (i.e., has the invite URL). Locking participant joins down to
 *     signed invites is a Phase 2 deliverable per the brief — until
 *     then the invite URL itself is the unguessable credential.
 */

import { NextResponse } from 'next/server';

import { issueJoinToken, MintTokenInputSchema, SessionNotFoundError } from '@/features/sessions';
import { auth } from '@/lib/auth';
import { orgIdForUser } from '@/lib/identity';
import { scopedDb } from '@/lib/scoped-db';

export async function POST(request: Request): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 });
  }
  const parsed = MintTokenInputSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'invalid_input', issues: parsed.error.issues },
      { status: 400 },
    );
  }

  if (parsed.data.role !== 'participant') {
    const userSession = await auth();
    if (!userSession?.user?.id) {
      return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
    }
    // Tenancy gate (researcher / moderator only): a cross-org caller
    // must 404 before any LiveKit token is minted. The participant path
    // skips this — the invite URL itself is intentionally bearer.
    const orgId = orgIdForUser(userSession.user.id);
    const owned = await scopedDb(orgId).sessions.findById(parsed.data.sessionId);
    if (owned === null) {
      return NextResponse.json({ error: 'session_not_found' }, { status: 404 });
    }
  }

  try {
    const result = await issueJoinToken(parsed.data);
    return NextResponse.json(result);
  } catch (err) {
    if (err instanceof SessionNotFoundError) {
      return NextResponse.json({ error: err.code }, { status: 404 });
    }
    throw err;
  }
}
