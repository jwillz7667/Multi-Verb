/**
 * POST /api/sessions/[id]/commands — researcher command publisher.
 *
 * Accepts a typed `ResearcherCommand` payload from the dashboard,
 * validates it, mints a fresh `command_id` server-side, and XADDs the
 * envelope onto the per-session Redis stream that the engine drains at
 * the top of each tick (brief §6 step 2, §11.3 transport).
 *
 * Status code map:
 *   - 200: command accepted; body returns `{ command_id, stream_entry_id }`
 *   - 400: malformed JSON body
 *   - 401: unauthenticated
 *   - 404: unknown session
 *   - 409: session has already ended / aborted
 *   - 422: payload validation failed; body returns the zod issue tree
 *
 * Node runtime: ioredis needs raw TCP, which Vercel Edge runtime forbids.
 */

import { NextResponse } from 'next/server';

import {
  publishResearcherCommand,
  PublishCommandInputSchema,
  SessionAlreadyEndedError,
  SessionNotFoundError,
} from '@/features/sessions';
import { auth } from '@/lib/auth';

import type { ZodError } from 'zod';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ id: string }>;
}

export async function POST(request: Request, context: RouteContext): Promise<NextResponse> {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  // Auth.js's `user.id` is the canonical researcher id in our schema
  // (it's the FK target for `researcher_actions.researcher_id`).
  const researcherId = session.user.id;
  if (researcherId === undefined || researcherId === '') {
    // Session present but user has no id — auth provider misconfig.
    // 401 rather than 500 because the request itself isn't malformed.
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  const { id: sessionId } = await context.params;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 });
  }

  const parsed = PublishCommandInputSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'invalid_payload', issues: zodIssueTree(parsed.error) },
      { status: 422 },
    );
  }

  try {
    const result = await publishResearcherCommand({
      sessionId,
      researcherId,
      input: parsed.data,
    });
    return NextResponse.json({
      ok: true,
      command_id: result.commandId,
      issued_at: result.issuedAt,
      stream_entry_id: result.streamEntryId,
    });
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

/**
 * Flatten zod issues into a wire-friendly shape the dashboard can map
 * onto form fields. Using `.issues` (not `.flatten()`) keeps the
 * discriminated-union variant path intact so a `set_quietness_budget`
 * error doesn't get attributed to `force_prompt.payload.prompt`.
 */
function zodIssueTree(err: ZodError): { path: (string | number)[]; message: string }[] {
  return err.issues.map((issue) => ({ path: issue.path, message: issue.message }));
}
