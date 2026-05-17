/**
 * Boundary tests for the rule-evaluations route.
 *
 * Pins:
 *   - Unauthenticated → 401.
 *   - Missing session → 404.
 *   - Decision belongs to a different session → 404 (cross-session smuggling guard).
 *   - Happy path → 200 with DTO-shaped evaluations.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

interface MockSession {
  user?: { id: string; email: string } | null;
}

interface FakeSessionRow {
  id: string;
  livekitRoomName: string;
  status: string;
  scheduledStart: Date | null;
  actualStart: Date | null;
  actualEnd: Date | null;
  createdAt: Date;
}

interface FakeScopedSessionRow {
  id: string;
  studyId: string;
  livekitRoomName: string;
  status: string;
  scheduledStart: Date | null;
  actualStart: Date | null;
  actualEnd: Date | null;
  createdAt: Date;
}

interface FakeRuleEvaluationRow {
  id: string;
  decisionId: string;
  ruleName: string;
  ruleVersion: string;
  fired: boolean;
  suppressedReason: string | null;
  predicateInputs: unknown;
  confidence: number;
}

const findSessionByIdMock = vi.fn<(id: string) => Promise<FakeSessionRow | null>>();
const findDecisionSessionIdMock = vi.fn<(decisionId: string) => Promise<string | null>>();
const listRuleEvaluationsForDecisionMock =
  vi.fn<(decisionId: string) => Promise<FakeRuleEvaluationRow[]>>();
const authMock = vi.fn<() => Promise<MockSession | null>>();
const scopedSessionsFindByIdMock =
  vi.fn<(sessionId: string) => Promise<FakeScopedSessionRow | null>>();

vi.mock('@/lib/auth', () => ({
  auth: (): Promise<MockSession | null> => authMock(),
}));

vi.mock('@/features/sessions', () => ({
  findSessionById: findSessionByIdMock,
  findDecisionSessionId: findDecisionSessionIdMock,
  listRuleEvaluationsForDecision: listRuleEvaluationsForDecisionMock,
}));

vi.mock('@/lib/scoped-db', () => ({
  scopedDb: (_orgId: string) => ({
    sessions: {
      findById: scopedSessionsFindByIdMock,
    },
  }),
}));

async function importRoute() {
  return import('./route');
}

function makeContext(id: string, decisionId: string) {
  return { params: Promise.resolve({ id, decisionId }) };
}

function makeSessionRow(id: string): FakeSessionRow {
  return {
    id,
    livekitRoomName: `room-${id}`,
    status: 'live',
    scheduledStart: null,
    actualStart: new Date(),
    actualEnd: null,
    createdAt: new Date(),
  };
}

function makeScopedSessionRow(id: string): FakeScopedSessionRow {
  return {
    id,
    studyId: 'study-1',
    livekitRoomName: `room-${id}`,
    status: 'live',
    scheduledStart: null,
    actualStart: new Date(),
    actualEnd: null,
    createdAt: new Date(),
  };
}

describe('GET /api/sessions/[id]/decisions/[decisionId]/evaluations', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 for an unauthenticated request', async () => {
    authMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/decisions/dec-1/evaluations'),
      makeContext('sess-1', 'dec-1'),
    );

    expect(res.status).toBe(401);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('unauthorized');
    expect(scopedSessionsFindByIdMock).not.toHaveBeenCalled();
    expect(findSessionByIdMock).not.toHaveBeenCalled();
    expect(listRuleEvaluationsForDecisionMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the session belongs to another org (scopedDb gate rejects before any decision read)', async () => {
    // Tenancy gate must reject before the decision lookup. Without it
    // a cross-org caller could probe decision ids — the same risk the
    // clip route mitigates by gating ahead of the flag lookup.
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/decisions/dec-1/evaluations'),
      makeContext('sess-1', 'dec-1'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_not_found');
    expect(findSessionByIdMock).not.toHaveBeenCalled();
    expect(findDecisionSessionIdMock).not.toHaveBeenCalled();
    expect(listRuleEvaluationsForDecisionMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the session is unknown', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow('missing'));
    findSessionByIdMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/missing/decisions/dec-1/evaluations'),
      makeContext('missing', 'dec-1'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_not_found');
    expect(findDecisionSessionIdMock).not.toHaveBeenCalled();
    expect(listRuleEvaluationsForDecisionMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the decision id is unknown', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow('sess-1'));
    findSessionByIdMock.mockResolvedValue(makeSessionRow('sess-1'));
    findDecisionSessionIdMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/decisions/dec-missing/evaluations'),
      makeContext('sess-1', 'dec-missing'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('decision_not_found');
    expect(listRuleEvaluationsForDecisionMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the decision belongs to a different session', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow('sess-1'));
    findSessionByIdMock.mockResolvedValue(makeSessionRow('sess-1'));
    // Cross-session smuggling attempt — decision lives under sess-other.
    findDecisionSessionIdMock.mockResolvedValue('sess-other');
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/decisions/dec-leaked/evaluations'),
      makeContext('sess-1', 'dec-leaked'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    // Same error code as "decision unknown" so the response can't
    // discriminate between "doesn't exist" and "exists elsewhere".
    expect(body.error).toBe('decision_not_found');
    expect(listRuleEvaluationsForDecisionMock).not.toHaveBeenCalled();
  });

  it('returns 200 with DTO-shaped evaluations for the owning session', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow('sess-1'));
    findSessionByIdMock.mockResolvedValue(makeSessionRow('sess-1'));
    findDecisionSessionIdMock.mockResolvedValue('sess-1');
    listRuleEvaluationsForDecisionMock.mockResolvedValue([
      {
        id: 'ev-1',
        decisionId: 'dec-1',
        ruleName: 'silence_gap',
        ruleVersion: '2026-01-01.1',
        fired: false,
        suppressedReason: null,
        // Predicate inputs are deliberately dropped from the DTO —
        // they're a snapshot of state that the dashboard doesn't render.
        predicateInputs: { silence_run_sec: 3.2 },
        confidence: 0.0,
      },
      {
        id: 'ev-2',
        decisionId: 'dec-1',
        ruleName: 'unheard_participant',
        ruleVersion: '2026-01-01.1',
        fired: true,
        suppressedReason: 'lower_priority_won',
        predicateInputs: { unheard_participant_id: 'p-3' },
        confidence: 0.72,
      },
    ]);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/decisions/dec-1/evaluations'),
      makeContext('sess-1', 'dec-1'),
    );

    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      decision_id: string;
      evaluations: Record<string, unknown>[];
    };
    expect(body.decision_id).toBe('dec-1');
    expect(body.evaluations).toHaveLength(2);
    expect(body.evaluations[0]).toEqual({
      id: 'ev-1',
      ruleName: 'silence_gap',
      ruleVersion: '2026-01-01.1',
      fired: false,
      suppressedReason: null,
      confidence: 0.0,
    });
    expect(body.evaluations[1]).toEqual({
      id: 'ev-2',
      ruleName: 'unheard_participant',
      ruleVersion: '2026-01-01.1',
      fired: true,
      suppressedReason: 'lower_priority_won',
      confidence: 0.72,
    });
    // The DTO must strip `predicateInputs` so the response stays small
    // and bounded — that field is a per-rule state dump that can balloon.
    for (const ev of body.evaluations) {
      expect(ev).not.toHaveProperty('predicateInputs');
      expect(ev).not.toHaveProperty('decisionId');
    }
    expect(listRuleEvaluationsForDecisionMock).toHaveBeenCalledWith('dec-1');
  });
});
