/**
 * POST /api/studies — create a new study.
 * GET  /api/studies — list studies for the current org.
 *
 * Studies own the persona + rules config + prompt the engine uses
 * (brief §7.5). The list endpoint is scoped to the caller's org so a
 * crafted request can never enumerate another tenant's research
 * questions.
 */

import { NextResponse } from 'next/server';

import { createNewStudy, CreateStudyInputSchema, listStudies } from '@/features/studies';
import { auth } from '@/lib/auth';
import { orgIdForUser, userUuidForAudit } from '@/lib/identity';

export async function POST(request: Request): Promise<NextResponse> {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    body = {};
  }
  const parsed = CreateStudyInputSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'invalid_input', issues: parsed.error.issues },
      { status: 400 },
    );
  }
  const created = await createNewStudy({
    input: parsed.data,
    orgId: orgIdForUser(session.user.id),
    createdBy: userUuidForAudit(session.user.id),
  });
  return NextResponse.json(created, { status: 201 });
}

export async function GET(): Promise<NextResponse> {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  const studies = await listStudies(orgIdForUser(session.user.id));
  return NextResponse.json({ studies });
}
