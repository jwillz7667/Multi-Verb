/**
 * GET   /api/studies/[id] — fetch one study (scoped to the caller's org).
 * PATCH /api/studies/[id] — update name / prompt / persona.
 *
 * Cross-tenant access returns 404, never 403 — a 403 would leak that
 * a study with that id exists for someone else. The same shape we use
 * for "study doesn't exist".
 */

import { NextResponse } from 'next/server';

import {
  getStudy,
  StudyNotFoundError,
  UpdateStudyInputSchema,
  updateExistingStudy,
} from '@/features/studies';
import { auth } from '@/lib/auth';
import { orgIdForUser } from '@/lib/identity';

interface Context {
  params: Promise<{ id: string }>;
}

export async function GET(_request: Request, context: Context): Promise<NextResponse> {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  const { id } = await context.params;
  const study = await getStudy(id, orgIdForUser(session.user.id));
  if (study === null) {
    return NextResponse.json({ error: 'study_not_found' }, { status: 404 });
  }
  return NextResponse.json(study);
}

export async function PATCH(request: Request, context: Context): Promise<NextResponse> {
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
  const parsed = UpdateStudyInputSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'invalid_input', issues: parsed.error.issues },
      { status: 400 },
    );
  }
  const { id } = await context.params;
  try {
    const updated = await updateExistingStudy({
      studyId: id,
      orgId: orgIdForUser(session.user.id),
      patch: parsed.data,
    });
    return NextResponse.json(updated);
  } catch (error) {
    if (error instanceof StudyNotFoundError) {
      return NextResponse.json({ error: 'study_not_found' }, { status: 404 });
    }
    throw error;
  }
}
