/**
 * POST /api/livekit/token — mint a participant token.
 *
 * Phase 1 access policy:
 *   - `researcher` and `moderator` roles require an authenticated user.
 *   - `participant` role is open to anyone who knows the session id
 *     (i.e., has the invite URL). Locking participant joins down to
 *     signed invites is a Phase 2 deliverable per the brief.
 */

import { NextResponse } from 'next/server';

import { issueJoinToken, MintTokenInputSchema, SessionNotFoundError } from '@/features/sessions';
import { auth } from '@/lib/auth';

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
    if (!userSession?.user) {
      return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
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
