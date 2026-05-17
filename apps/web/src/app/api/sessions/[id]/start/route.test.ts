/**
 * Boundary tests for the start (LiveKit dispatch) route.
 *
 * Pins:
 *   - Unauthenticated → 401, no dispatch attempted.
 *   - Cross-org caller → 404 with the same "not found" body as a
 *     legit missing id, and `startSession` (which dispatches the agent)
 *     is never called.
 *   - Happy path → 200 returning the session row.
 *   - SessionNotFoundError → 404, SessionAlreadyEndedError → 409.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

interface MockSession {
  user?: { id?: string; email?: string } | null;
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

const authMock = vi.fn<() => Promise<MockSession | null>>();
const startSessionMock = vi.fn<(id: string) => Promise<FakeSessionRow>>();
const scopedSessionsFindByIdMock =
  vi.fn<(sessionId: string) => Promise<FakeScopedSessionRow | null>>();

class FakeSessionNotFoundError extends Error {
  readonly code = 'session_not_found';
  constructor(public readonly sessionId: string) {
    super(`Session ${sessionId} not found`);
    this.name = 'SessionNotFoundError';
  }
}

class FakeSessionAlreadyEndedError extends Error {
  readonly code = 'session_already_ended';
  constructor(public readonly sessionId: string) {
    super(`Session ${sessionId} has already ended`);
    this.name = 'SessionAlreadyEndedError';
  }
}

vi.mock('@/lib/auth', () => ({
  auth: (): Promise<MockSession | null> => authMock(),
}));

vi.mock('@/features/sessions', () => ({
  startSession: startSessionMock,
  SessionNotFoundError: FakeSessionNotFoundError,
  SessionAlreadyEndedError: FakeSessionAlreadyEndedError,
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

function makeContext(id: string) {
  return { params: Promise.resolve({ id }) };
}

function makeScopedSessionRow(id: string): FakeScopedSessionRow {
  return {
    id,
    studyId: 'study-1',
    livekitRoomName: `room-${id}`,
    status: 'scheduled',
    scheduledStart: null,
    actualStart: null,
    actualEnd: null,
    createdAt: new Date(),
  };
}

function makeSessionRow(id: string): FakeSessionRow {
  return {
    id,
    livekitRoomName: `room-${id}`,
    status: 'scheduled',
    scheduledStart: null,
    actualStart: null,
    actualEnd: null,
    createdAt: new Date(),
  };
}

describe('POST /api/sessions/[id]/start', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 for an unauthenticated request', async () => {
    authMock.mockResolvedValue(null);
    const { POST } = await importRoute();

    const res = await POST(
      new Request('http://localhost/api/sessions/sess-1/start', { method: 'POST' }),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(401);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('unauthorized');
    expect(scopedSessionsFindByIdMock).not.toHaveBeenCalled();
    expect(startSessionMock).not.toHaveBeenCalled();
  });

  it('returns 401 when the session has no user id (auth provider misconfig)', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    const { POST } = await importRoute();

    const res = await POST(
      new Request('http://localhost/api/sessions/sess-1/start', { method: 'POST' }),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(401);
    expect(scopedSessionsFindByIdMock).not.toHaveBeenCalled();
    expect(startSessionMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the session belongs to another org (scopedDb gate rejects before LiveKit dispatch)', async () => {
    // Without this gate a cross-org caller could (a) trigger LiveKit
    // agent dispatch into another tenant's room and (b) read the
    // session row back in the response body. Both are silent breaches.
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(null);
    const { POST } = await importRoute();

    const res = await POST(
      new Request('http://localhost/api/sessions/sess-other/start', { method: 'POST' }),
      makeContext('sess-other'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_not_found');
    expect(startSessionMock).not.toHaveBeenCalled();
  });

  it('returns 200 and the session row on successful dispatch', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow('sess-1'));
    startSessionMock.mockResolvedValue(makeSessionRow('sess-1'));
    const { POST } = await importRoute();

    const res = await POST(
      new Request('http://localhost/api/sessions/sess-1/start', { method: 'POST' }),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(200);
    const body = (await res.json()) as { ok: boolean; session: { id: string } };
    expect(body.ok).toBe(true);
    expect(body.session.id).toBe('sess-1');
    expect(startSessionMock).toHaveBeenCalledWith('sess-1');
  });

  it('returns 404 when startSession throws SessionNotFoundError (race window)', async () => {
    // Row was visible to the gate but vanished before LiveKit dispatch —
    // 404 stays consistent with the gate's response.
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow('sess-1'));
    startSessionMock.mockRejectedValue(new FakeSessionNotFoundError('sess-1'));
    const { POST } = await importRoute();

    const res = await POST(
      new Request('http://localhost/api/sessions/sess-1/start', { method: 'POST' }),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_not_found');
  });

  it('returns 409 when the session has already ended', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow('done'));
    startSessionMock.mockRejectedValue(new FakeSessionAlreadyEndedError('done'));
    const { POST } = await importRoute();

    const res = await POST(
      new Request('http://localhost/api/sessions/done/start', { method: 'POST' }),
      makeContext('done'),
    );

    expect(res.status).toBe(409);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_already_ended');
  });
});
