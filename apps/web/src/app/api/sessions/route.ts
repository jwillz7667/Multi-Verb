/**
 * POST /api/sessions — create a new moderated session.
 * GET  /api/sessions — list recent sessions (auth-gated).
 *
 * Researchers create sessions here. The agent isn't dispatched yet —
 * that happens via `POST /api/sessions/[id]/start` so we don't burn
 * engine resources on sessions that get abandoned mid-form.
 */

import { NextResponse } from 'next/server';

import {
  createNewSession,
  CreateSessionInputSchema,
  listRecentSessions,
} from '@/features/sessions';
import { auth } from '@/lib/auth';

export async function POST(request: Request): Promise<NextResponse> {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    body = {};
  }
  const parsed = CreateSessionInputSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'invalid_input', issues: parsed.error.issues },
      { status: 400 },
    );
  }
  const created = await createNewSession(parsed.data);
  return NextResponse.json(created, { status: 201 });
}

export async function GET(): Promise<NextResponse> {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  const rows = await listRecentSessions();
  return NextResponse.json({ sessions: rows });
}
