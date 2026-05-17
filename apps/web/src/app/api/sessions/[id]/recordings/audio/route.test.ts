/**
 * Boundary tests for GET /api/sessions/[id]/recordings/audio.
 *
 * Pins:
 *   - Unauthenticated → 401, no DB / signer touched.
 *   - Unknown session → 404.
 *   - Session without composite recording, no participant query → 404.
 *   - Happy composite path → 200 with signed URL + expires_at.
 *   - Happy participant path → 200 with signed URL + identity echoed.
 *   - Participant identity not in the JSON map → 404 (collapses with
 *     "session has no per-participant track at all" so the response
 *     can't enumerate which identities have audio).
 *   - signGetUrl throws R2NotConfiguredError → 503 so ops can triage.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type * as ReplayUrlsModule from '@/features/recordings/replay-urls';

interface MockUserSession {
  user?: { email: string } | null;
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

class FakeR2NotConfiguredError extends Error {
  readonly missing: readonly string[];
  constructor(missing: readonly string[]) {
    super(`not configured: ${missing.join(',')}`);
    this.name = 'R2NotConfiguredError';
    this.missing = missing;
  }
}

const findSessionForReplayMock = vi.fn<(id: string) => Promise<FakeSessionRow | null>>();
const signGetUrlMock = vi.fn<(key: string, ttl?: number) => Promise<string>>();
const authMock = vi.fn<() => Promise<MockUserSession | null>>();

vi.mock('@/lib/auth', () => ({
  auth: (): Promise<MockUserSession | null> => authMock(),
}));

vi.mock('@/features/sessions', () => ({
  findSessionForReplay: findSessionForReplayMock,
}));

vi.mock('@/features/recordings', async () => {
  // Re-use the real `parseParticipantRecordingUrls` — it's a pure
  // function with its own unit tests and mocking it would just
  // duplicate that contract here.
  const real = await vi.importActual<typeof ReplayUrlsModule>('@/features/recordings/replay-urls');
  return {
    parseParticipantRecordingUrls: real.parseParticipantRecordingUrls,
    signGetUrl: signGetUrlMock,
    R2NotConfiguredError: FakeR2NotConfiguredError,
  };
});

async function importRoute() {
  return import('./route');
}

function makeContext(id: string) {
  return { params: Promise.resolve({ id }) };
}

function makeSessionRow(overrides: Partial<FakeSessionRow> = {}): FakeSessionRow {
  return {
    id: 'sess-1',
    livekitRoomName: 'room-1',
    status: 'ended',
    scheduledStart: null,
    actualStart: new Date('2026-05-01T10:00:00Z'),
    actualEnd: new Date('2026-05-01T11:00:00Z'),
    createdAt: new Date('2026-05-01T09:50:00Z'),
    recordingUrl: 'sessions/sess-1/composite.mp4',
    perParticipantRecordingUrls: {
      'alice-001': 'sessions/sess-1/participants/alice.mp4',
    },
    configSnapshot: {},
    ...overrides,
  };
}

describe('GET /api/sessions/[id]/recordings/audio', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 for an unauthenticated request', async () => {
    authMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/recordings/audio'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(401);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('unauthorized');
    expect(findSessionForReplayMock).not.toHaveBeenCalled();
    expect(signGetUrlMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the session does not exist', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionForReplayMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/missing/recordings/audio'),
      makeContext('missing'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_not_found');
    expect(signGetUrlMock).not.toHaveBeenCalled();
  });

  it('returns 404 when no composite recording is present', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionForReplayMock.mockResolvedValue(makeSessionRow({ recordingUrl: null }));
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/recordings/audio'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('recording_not_found');
    expect(signGetUrlMock).not.toHaveBeenCalled();
  });

  it('returns 200 with a signed URL for the composite recording', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionForReplayMock.mockResolvedValue(makeSessionRow());
    signGetUrlMock.mockResolvedValue('https://r2.example/sessions/sess-1/composite.mp4?sig=xyz');
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/recordings/audio'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      url: string;
      expires_at: string;
      kind: string;
      participant_identity?: string;
    };
    expect(body.url).toBe('https://r2.example/sessions/sess-1/composite.mp4?sig=xyz');
    expect(body.kind).toBe('composite');
    expect(body.participant_identity).toBeUndefined();
    expect(Date.parse(body.expires_at)).not.toBeNaN();
    expect(signGetUrlMock).toHaveBeenCalledWith('sessions/sess-1/composite.mp4', 60 * 60);
  });

  it('returns 200 with a signed URL for a per-participant track', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionForReplayMock.mockResolvedValue(makeSessionRow());
    signGetUrlMock.mockResolvedValue(
      'https://r2.example/sessions/sess-1/participants/alice.mp4?sig=abc',
    );
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/recordings/audio?participant=alice-001'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      url: string;
      kind: string;
      participant_identity?: string;
    };
    expect(body.kind).toBe('participant');
    expect(body.participant_identity).toBe('alice-001');
    expect(signGetUrlMock).toHaveBeenCalledWith('sessions/sess-1/participants/alice.mp4', 60 * 60);
  });

  it('returns 404 when the requested participant identity has no recording', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionForReplayMock.mockResolvedValue(makeSessionRow());
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/recordings/audio?participant=unknown-999'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    // Same code as "no composite" so the route can't be used to
    // enumerate which participants have a track recorded.
    expect(body.error).toBe('recording_not_found');
    expect(signGetUrlMock).not.toHaveBeenCalled();
  });

  it('returns 503 when R2 is not configured at sign time', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionForReplayMock.mockResolvedValue(makeSessionRow());
    signGetUrlMock.mockRejectedValue(new FakeR2NotConfiguredError(['R2_BUCKET']));
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/recordings/audio'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(503);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('r2_not_configured');
  });

  it('treats an empty `participant=` query value as a composite request', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionForReplayMock.mockResolvedValue(makeSessionRow());
    signGetUrlMock.mockResolvedValue('https://r2.example/composite.mp4?sig=z');
    const { GET } = await importRoute();

    // `?participant=` with no value should fall back to the composite
    // recording — the alternative (treating it as "lookup empty
    // identity") would always 404 and produce confusing UX.
    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/recordings/audio?participant='),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(200);
    const body = (await res.json()) as { kind: string };
    expect(body.kind).toBe('composite');
  });
});
