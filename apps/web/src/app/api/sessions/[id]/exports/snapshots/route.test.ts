/**
 * Boundary tests for the state-snapshots JSONL export route.
 *
 * Pins:
 *   - Unauthenticated → 401, no DB calls.
 *   - Unknown session → 404.
 *   - 200 with `application/x-ndjson` content-type + download filename.
 *   - Each body line is a valid JSON object carrying ts / tick_id / state.
 *   - Empty result → empty body (no spurious whitespace).
 *   - Filename is sanitised for slashes / quotes / spaces.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ReplaySessionRow, StateSnapshotRow } from '@/features/sessions';

interface MockSession {
  user?: { id: string; email: string } | null;
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
const findSessionForReplayMock = vi.fn<(id: string) => Promise<ReplaySessionRow | null>>();
const iterateAllSnapshotsForSessionMock =
  vi.fn<(id: string) => AsyncGenerator<StateSnapshotRow[], void, void>>();
const scopedSessionsFindByIdMock =
  vi.fn<(sessionId: string) => Promise<FakeScopedSessionRow | null>>();

vi.mock('@/lib/auth', () => ({
  auth: (): Promise<MockSession | null> => authMock(),
}));

vi.mock('@/features/sessions', () => ({
  findSessionForReplay: findSessionForReplayMock,
  iterateAllSnapshotsForSession: iterateAllSnapshotsForSessionMock,
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

function makeScopedSession(): FakeScopedSessionRow {
  return {
    id: 'sess-12345678abcdef',
    studyId: 'study-1',
    livekitRoomName: 'room-alpha',
    status: 'ended',
    scheduledStart: null,
    actualStart: new Date('2026-05-01T10:00:00.000Z'),
    actualEnd: new Date('2026-05-01T10:00:30.000Z'),
    createdAt: new Date('2026-05-01T09:55:00.000Z'),
  };
}

function makeSession(overrides: Partial<ReplaySessionRow> = {}): ReplaySessionRow {
  return {
    id: 'sess-12345678abcdef',
    livekitRoomName: 'room-alpha',
    status: 'ended',
    scheduledStart: null,
    actualStart: new Date('2026-05-01T10:00:00.000Z'),
    actualEnd: new Date('2026-05-01T10:00:30.000Z'),
    createdAt: new Date('2026-05-01T09:55:00.000Z'),
    recordingUrl: null,
    perParticipantRecordingUrls: {},
    configSnapshot: {},
    ...overrides,
  };
}

function makeSnapshot(overrides: Partial<StateSnapshotRow>): StateSnapshotRow {
  return {
    id: 's-default',
    sessionId: 'sess-12345678abcdef',
    tickId: 0n,
    ts: new Date('2026-05-01T10:00:00.000Z'),
    state: { participants: {}, time_remaining_sec: 3600 },
    ...overrides,
  };
}

function generatorOf<T>(chunks: T[][]): () => AsyncGenerator<T[], void, void> {
  return async function* () {
    await Promise.resolve();
    for (const chunk of chunks) {
      yield chunk;
    }
  };
}

describe('GET /api/sessions/[id]/exports/snapshots', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 for an unauthenticated request and skips the DB lookups', async () => {
    authMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/exports/snapshots'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(401);
    expect(scopedSessionsFindByIdMock).not.toHaveBeenCalled();
    expect(findSessionForReplayMock).not.toHaveBeenCalled();
    expect(iterateAllSnapshotsForSessionMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the session belongs to another org (scopedDb gate filters it)', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/exports/snapshots'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(404);
    expect(findSessionForReplayMock).not.toHaveBeenCalled();
    expect(iterateAllSnapshotsForSessionMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the session is unknown', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSession());
    findSessionForReplayMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/missing/exports/snapshots'),
      makeContext('missing'),
    );

    expect(res.status).toBe(404);
    expect(iterateAllSnapshotsForSessionMock).not.toHaveBeenCalled();
  });

  it('returns 200 application/x-ndjson with one JSON object per line and a download filename', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSession());
    findSessionForReplayMock.mockResolvedValue(makeSession());
    iterateAllSnapshotsForSessionMock.mockImplementation(
      generatorOf([
        [
          makeSnapshot({
            id: 's-1',
            tickId: 0n,
            ts: new Date('2026-05-01T10:00:00.000Z'),
            state: { phase: 'open' },
          }),
          makeSnapshot({
            id: 's-2',
            tickId: 1n,
            ts: new Date('2026-05-01T10:00:00.500Z'),
            state: { phase: 'middle' },
          }),
        ],
      ]),
    );

    const { GET } = await importRoute();
    const res = await GET(
      new Request('http://localhost/api/sessions/sess-12345678abcdef/exports/snapshots'),
      makeContext('sess-12345678abcdef'),
    );

    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('application/x-ndjson; charset=utf-8');
    expect(res.headers.get('content-disposition')).toBe(
      'attachment; filename="snapshots-room-alpha-sess-1234.jsonl"',
    );

    const body = await res.text();
    const lines = body.trimEnd().split('\n');
    expect(lines).toHaveLength(2);

    const first = JSON.parse(lines[0] ?? '') as {
      ts: string;
      tick_id: number;
      state: { phase: string };
    };
    expect(first.ts).toBe('2026-05-01T10:00:00.000Z');
    expect(first.tick_id).toBe(0);
    expect(first.state.phase).toBe('open');
  });

  it('returns 200 with an empty body when the session has no snapshots yet', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSession());
    findSessionForReplayMock.mockResolvedValue(makeSession());
    iterateAllSnapshotsForSessionMock.mockImplementation(generatorOf([]));

    const { GET } = await importRoute();
    const res = await GET(
      new Request('http://localhost/api/sessions/sess-12345678abcdef/exports/snapshots'),
      makeContext('sess-12345678abcdef'),
    );

    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toBe('');
  });

  it('sanitises filename so room names with slashes or quotes become safe', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSession());
    findSessionForReplayMock.mockResolvedValue(
      makeSession({ livekitRoomName: 'room/with spaces & "quotes"' }),
    );
    iterateAllSnapshotsForSessionMock.mockImplementation(generatorOf([[]]));

    const { GET } = await importRoute();
    const res = await GET(
      new Request('http://localhost/api/sessions/sess-12345678abcdef/exports/snapshots'),
      makeContext('sess-12345678abcdef'),
    );

    const cd = res.headers.get('content-disposition');
    expect(cd).toBe('attachment; filename="snapshots-room-with-spaces----quotes--sess-1234.jsonl"');
    const match = /filename="([^"]+)"/.exec(cd ?? '');
    expect(match?.[1] ?? '').not.toMatch(/[/&"]/);
  });
});
