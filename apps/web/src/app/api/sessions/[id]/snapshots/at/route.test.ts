/**
 * Boundary tests for GET /api/sessions/[id]/snapshots/at.
 *
 * Pins:
 *   - Unauthenticated → 401, no DB lookup attempted.
 *   - Missing `ts` query param → 400.
 *   - Unparseable `ts` → 400.
 *   - Cross-org session (scopedDb returns null) → 404; the wider
 *     replay projection is never loaded.
 *   - Unknown session → 404.
 *   - No snapshot ≤ ts → 404 (scrubber landed before first tick).
 *   - Happy path → 200 with the wire DTO; bigint tick_id is coerced
 *     to a JS number for JSON.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

interface MockUserSession {
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
  recordingUrl: string | null;
  perParticipantRecordingUrls: unknown;
  configSnapshot: unknown;
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

interface FakeSnapshotRow {
  id: string;
  sessionId: string;
  tickId: bigint;
  ts: Date;
  state: unknown;
}

const authMock = vi.fn<() => Promise<MockUserSession | null>>();
const findSessionForReplayMock = vi.fn<(id: string) => Promise<FakeSessionRow | null>>();
const findSnapshotAtOrBeforeMock =
  vi.fn<(sessionId: string, ts: Date) => Promise<FakeSnapshotRow | null>>();
const scopedSessionsFindByIdMock =
  vi.fn<(sessionId: string) => Promise<FakeScopedSessionRow | null>>();

vi.mock('@/lib/auth', () => ({
  auth: (): Promise<MockUserSession | null> => authMock(),
}));

vi.mock('@/features/sessions', () => ({
  findSessionForReplay: findSessionForReplayMock,
  findSnapshotAtOrBefore: findSnapshotAtOrBeforeMock,
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

function makeSessionRow(): FakeSessionRow {
  return {
    id: 'sess-1',
    livekitRoomName: 'room-1',
    status: 'ended',
    scheduledStart: null,
    actualStart: new Date('2026-05-01T10:00:00Z'),
    actualEnd: new Date('2026-05-01T11:00:00Z'),
    createdAt: new Date('2026-05-01T09:50:00Z'),
    recordingUrl: null,
    perParticipantRecordingUrls: {},
    configSnapshot: {},
  };
}

function makeScopedSessionRow(): FakeScopedSessionRow {
  return {
    id: 'sess-1',
    studyId: 'study-1',
    livekitRoomName: 'room-1',
    status: 'ended',
    scheduledStart: null,
    actualStart: new Date('2026-05-01T10:00:00Z'),
    actualEnd: new Date('2026-05-01T11:00:00Z'),
    createdAt: new Date('2026-05-01T09:50:00Z'),
  };
}

describe('GET /api/sessions/[id]/snapshots/at', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 when unauthenticated and never hits the DB', async () => {
    authMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/snapshots/at?ts=2026-05-01T10:00:00Z'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(401);
    expect(scopedSessionsFindByIdMock).not.toHaveBeenCalled();
    expect(findSessionForReplayMock).not.toHaveBeenCalled();
    expect(findSnapshotAtOrBeforeMock).not.toHaveBeenCalled();
  });

  it('returns 400 when the ts query param is missing', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/snapshots/at'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('ts_required');
    expect(scopedSessionsFindByIdMock).not.toHaveBeenCalled();
    expect(findSessionForReplayMock).not.toHaveBeenCalled();
  });

  it('returns 400 when ts is not a valid date', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/snapshots/at?ts=garbage'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('ts_invalid');
  });

  it('returns 404 when the session belongs to another org (scopedDb returns null)', async () => {
    // Cross-tenant access: the scopedDb gate filters it out before the
    // replay projection is even attempted. The replay helper must not
    // be invoked — otherwise we'd be leaking a single unauthorized read
    // per request (and the tenant guard would only catch the second one).
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/snapshots/at?ts=2026-05-01T10:00:00Z'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_not_found');
    expect(scopedSessionsFindByIdMock).toHaveBeenCalledWith('sess-1');
    expect(findSessionForReplayMock).not.toHaveBeenCalled();
    expect(findSnapshotAtOrBeforeMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the session does not exist', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow());
    findSessionForReplayMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/missing/snapshots/at?ts=2026-05-01T10:00:00Z'),
      makeContext('missing'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_not_found');
    expect(findSnapshotAtOrBeforeMock).not.toHaveBeenCalled();
  });

  it('returns 404 when no snapshot ≤ ts exists', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow());
    findSessionForReplayMock.mockResolvedValue(makeSessionRow());
    findSnapshotAtOrBeforeMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/snapshots/at?ts=2026-05-01T09:59:00Z'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('no_snapshot_at_ts');
  });

  it('returns 200 with the snapshot DTO; bigint tick_id is coerced to number', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow());
    findSessionForReplayMock.mockResolvedValue(makeSessionRow());
    findSnapshotAtOrBeforeMock.mockResolvedValue({
      id: 'snap-1',
      sessionId: 'sess-1',
      tickId: 1234n,
      ts: new Date('2026-05-01T10:05:00.000Z'),
      state: { session_id: 'sess-1', tick_id: 1234, elapsed_sec: 300, t: '2026-05-01T10:05:00Z' },
    });
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/snapshots/at?ts=2026-05-01T10:05:30Z'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      id: string;
      tick_id: number;
      ts: string;
      state: { session_id: string; tick_id: number };
    };
    expect(body.id).toBe('snap-1');
    expect(typeof body.tick_id).toBe('number');
    expect(body.tick_id).toBe(1234);
    expect(body.ts).toBe('2026-05-01T10:05:00.000Z');
    expect(body.state.session_id).toBe('sess-1');
    expect(findSnapshotAtOrBeforeMock).toHaveBeenCalledWith(
      'sess-1',
      new Date('2026-05-01T10:05:30.000Z'),
    );
  });
});
