/**
 * GET /api/sessions/[id]/decisions/[decisionId]/evaluations
 *
 * Returns the per-rule `rule_evaluations` rows tied to one decision —
 * the audit trail that powers the "Why quiet now?" panel (Phase 3 L12).
 *
 * Why a dedicated endpoint instead of riding the SSE wire: a v1 study
 * runs ~7200 ticks/hour × 6 rules ≈ 43k evaluation rows per session.
 * Streaming every row to every connected dashboard would burn
 * bandwidth no researcher will look at — the typical inspection is
 * one decision at a time. On-demand fetch keeps the live wire lean.
 *
 * Security: a `decision_id` is a UUID that maps to exactly one
 * session, so the route resolves the decision's owning session first
 * and returns 404 if it doesn't match the path's session id. This
 * prevents cross-session smuggling: a researcher who can read
 * session A must not be able to pull decision evaluations from
 * session B by guessing or harvesting a `decision_id`.
 */

import { NextResponse } from 'next/server';

import {
  findDecisionSessionId,
  findSessionById,
  listRuleEvaluationsForDecision,
} from '@/features/sessions';
import type { RuleEvaluationRow } from '@/features/sessions';
import { auth } from '@/lib/auth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ id: string; decisionId: string }>;
}

interface EvaluationDTO {
  id: string;
  ruleName: string;
  ruleVersion: string;
  fired: boolean;
  suppressedReason: string | null;
  confidence: number;
}

interface EvaluationsResponse {
  decision_id: string;
  evaluations: EvaluationDTO[];
}

export async function GET(_request: Request, context: RouteContext): Promise<NextResponse> {
  const userSession = await auth();
  if (!userSession?.user) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  const { id: sessionId, decisionId } = await context.params;

  // Belt-and-braces: resolve the session row first so an unknown id
  // returns the same 404 the rest of the dashboard returns. Doing this
  // BEFORE the decision lookup keeps the response shape and timing
  // identical whether the session is missing or the decision is.
  const session = await findSessionById(sessionId);
  if (session === null) {
    return NextResponse.json({ error: 'session_not_found' }, { status: 404 });
  }

  const owningSessionId = await findDecisionSessionId(decisionId);
  // Two failure modes collapse to the same 404 so the response never
  // reveals whether the decision exists at all in another session.
  if (owningSessionId === null || owningSessionId !== sessionId) {
    return NextResponse.json({ error: 'decision_not_found' }, { status: 404 });
  }

  const rows = await listRuleEvaluationsForDecision(decisionId);
  const body: EvaluationsResponse = {
    decision_id: decisionId,
    evaluations: rows.map(toDTO),
  };
  return NextResponse.json(body);
}

function toDTO(row: RuleEvaluationRow): EvaluationDTO {
  return {
    id: row.id,
    ruleName: row.ruleName,
    ruleVersion: row.ruleVersion,
    fired: row.fired,
    suppressedReason: row.suppressedReason,
    confidence: row.confidence,
  };
}
