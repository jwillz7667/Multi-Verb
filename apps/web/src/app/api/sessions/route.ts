/**
 * POST /api/sessions — create a new moderated session.
 * GET  /api/sessions — list recent sessions in the caller's org.
 *
 * Researchers create sessions here. The agent isn't dispatched yet —
 * that happens via `POST /api/sessions/[id]/start` so we don't burn
 * engine resources on sessions that get abandoned mid-form.
 *
 * The list is tenant-scoped via `scopedDb(orgId).sessions.listRecent`
 * so a researcher in org A can never enumerate org B's sessions.
 * Orphan sessions (studyId === null) are intentionally absent — they
 * only surface through the admin retention sweep.
 */

import { NextResponse } from 'next/server';

import { createNewSession, CreateSessionInputSchema } from '@/features/sessions';
import { auth } from '@/lib/auth';
import { orgIdForUser } from '@/lib/identity';
import { scopedDb } from '@/lib/scoped-db';

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
  if (!session?.user?.id) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  const orgId = orgIdForUser(session.user.id);
  const rows = await scopedDb(orgId).sessions.listRecent();
  return NextResponse.json({
    sessions: rows.map((row) => ({
      id: row.id,
      livekitRoomName: row.livekitRoomName,
      status: row.status,
      scheduledStart: row.scheduledStart,
      actualStart: row.actualStart,
      actualEnd: row.actualEnd,
      createdAt: row.createdAt,
    })),
  });
}
