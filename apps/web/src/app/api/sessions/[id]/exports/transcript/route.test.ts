/**
 * Boundary tests for the transcript-export route.
 *
 * Pins:
 *   - Unauthenticated → 401, no DB calls.
 *   - Invalid format → 400 (only "txt" and "vtt" allowed).
 *   - Unknown session → 404.
 *   - 200 with `text/plain` for ?format=txt, `text/vtt` for ?format=vtt.
 *   - Filename uses room name + short id and is download-safe.
 *   - The body contains both a participant utterance and a moderator
 *     turn, ordered by start time, so the wiring through to the
 *     formatter is verified end-to-end.
 *   - Anchor falls back to `createdAt` when `actualStart` is null.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  DecisionRow,
  ParticipantRow,
  ReplaySessionRow,
  UtteranceWithSpeakerRow,
} from '@/features/sessions';

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
const listParticipantsForSessionMock = vi.fn<(id: string) => Promise<ParticipantRow[]>>();
const listExecutedModeratorTurnsMock = vi.fn<(id: string) => Promise<DecisionRow[]>>();
// The iterator helper is mocked as a generator that yields one
// pre-built chunk per call.
const iterateAllUtterancesForSessionMock =
  vi.fn<(id: string) => AsyncGenerator<UtteranceWithSpeakerRow[], void, void>>();
const scopedSessionsFindByIdMock =
  vi.fn<(sessionId: string) => Promise<FakeScopedSessionRow | null>>();

vi.mock('@/lib/auth', () => ({
  auth: (): Promise<MockSession | null> => authMock(),
}));

vi.mock('@/features/sessions', () => ({
  findSessionForReplay: findSessionForReplayMock,
  listParticipantsForSession: listParticipantsForSessionMock,
  listExecutedModeratorTurns: listExecutedModeratorTurnsMock,
  iterateAllUtterancesForSession: iterateAllUtterancesForSessionMock,
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

function makeParticipant(overrides: Partial<ParticipantRow>): ParticipantRow {
  return {
    id: 'p-default',
    sessionId: 'sess-12345678abcdef',
    displayName: 'Default',
    role: 'participant',
    joinedAt: new Date('2026-05-01T10:00:00.000Z'),
    leftAt: null,
    livekitIdentity: 'default-001',
    ...overrides,
  };
}

function makeUtterance(overrides: Partial<UtteranceWithSpeakerRow>): UtteranceWithSpeakerRow {
  return {
    id: 'u-default',
    sessionId: 'sess-12345678abcdef',
    participantId: 'p-alice',
    participantIdentity: 'alice-001',
    participantDisplayName: 'Alice',
    text: 'hi everyone',
    isFinal: true,
    confidence: 0.95,
    startTs: new Date('2026-05-01T10:00:05.000Z'),
    endTs: new Date('2026-05-01T10:00:07.000Z'),
    ...overrides,
  };
}

function makeDecision(overrides: Partial<DecisionRow>): DecisionRow {
  return {
    id: 'd-default',
    sessionId: 'sess-12345678abcdef',
    tickId: 0n,
    ts: new Date('2026-05-01T10:00:10.000Z'),
    action: 'prompt',
    targetParticipantId: null,
    source: 'rules_engine',
    triggeringRule: 'unheard_participant',
    researcherId: null,
    researcherHint: null,
    reasonCodes: [],
    reasonHuman: '',
    confidence: 0.7,
    suppressedBy: [],
    wasExecuted: true,
    llmPrompt: null,
    llmOutput: 'Bob, your take?',
    ttsAudioUrl: null,
    spokenAt: new Date('2026-05-01T10:00:10.250Z'),
    cooldownUntil: new Date('2026-05-01T10:00:40.000Z'),
    ...overrides,
  };
}

function generatorOf<T>(chunks: T[][]): () => AsyncGenerator<T[], void, void> {
  return async function* () {
    // The production iterator awaits each Prisma cursor page; the mock
    // yields pre-built chunks synchronously, so a no-op await here
    // satisfies `require-await` without changing observable behavior.
    await Promise.resolve();
    for (const chunk of chunks) {
      yield chunk;
    }
  };
}

describe('GET /api/sessions/[id]/exports/transcript', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 for an unauthenticated request and skips the DB lookups', async () => {
    authMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/exports/transcript'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(401);
    expect(scopedSessionsFindByIdMock).not.toHaveBeenCalled();
    expect(findSessionForReplayMock).not.toHaveBeenCalled();
    expect(iterateAllUtterancesForSessionMock).not.toHaveBeenCalled();
  });

  it('returns 400 for an unsupported format', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/exports/transcript?format=pdf'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('invalid_format');
    // Validation runs before the tenancy gate so an invalid request
    // never causes a DB roundtrip — keep 400s cheap.
    expect(scopedSessionsFindByIdMock).not.toHaveBeenCalled();
    expect(findSessionForReplayMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the session belongs to another org (scopedDb gate filters it)', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/exports/transcript'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_not_found');
    expect(findSessionForReplayMock).not.toHaveBeenCalled();
    expect(iterateAllUtterancesForSessionMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the session is unknown', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSession());
    findSessionForReplayMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/missing/exports/transcript'),
      makeContext('missing'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_not_found');
    expect(iterateAllUtterancesForSessionMock).not.toHaveBeenCalled();
  });

  it('returns 200 text/plain with the interleaved transcript and a download filename for format=txt', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSession());
    findSessionForReplayMock.mockResolvedValue(makeSession());
    listParticipantsForSessionMock.mockResolvedValue([
      makeParticipant({ id: 'p-alice', displayName: 'Alice', livekitIdentity: 'alice-001' }),
      makeParticipant({ id: 'p-bob', displayName: 'Bob', livekitIdentity: 'bob-002' }),
      makeParticipant({
        id: 'p-mod',
        displayName: 'Moderator',
        role: 'moderator',
        livekitIdentity: 'mod',
      }),
    ]);
    listExecutedModeratorTurnsMock.mockResolvedValue([makeDecision({})]);
    iterateAllUtterancesForSessionMock.mockImplementation(
      generatorOf([
        [
          makeUtterance({
            id: 'u-1',
            text: 'hi everyone',
            startTs: new Date('2026-05-01T10:00:05.000Z'),
            endTs: new Date('2026-05-01T10:00:06.500Z'),
          }),
          makeUtterance({
            id: 'u-2',
            participantId: 'p-bob',
            participantIdentity: 'bob-002',
            participantDisplayName: 'Bob',
            text: 'thanks for having us',
            startTs: new Date('2026-05-01T10:00:15.000Z'),
            endTs: new Date('2026-05-01T10:00:17.000Z'),
          }),
        ],
      ]),
    );
    const { GET } = await importRoute();

    const res = await GET(
      new Request(
        'http://localhost/api/sessions/sess-12345678abcdef/exports/transcript?format=txt',
      ),
      makeContext('sess-12345678abcdef'),
    );

    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('text/plain; charset=utf-8');
    const cd = res.headers.get('content-disposition');
    expect(cd).toContain('attachment');
    expect(cd).toContain('transcript-room-alpha-sess-1234.txt');

    const body = await res.text();
    // Header block + interleaved cues.
    expect(body).toContain('# Session: room-alpha (sess-12345678abcdef)');
    // Moderator excluded from the participants list (role filter).
    expect(body).toContain('# Participants: Alice, Bob');
    expect(body).toContain('[00:00:05] Alice: hi everyone');
    expect(body).toContain('[00:00:10] Moderator: Bob, your take?');
    expect(body).toContain('[00:00:15] Bob: thanks for having us');
    // Order: Alice → Moderator → Bob.
    const alicePos = body.indexOf('Alice: hi everyone');
    const modPos = body.indexOf('Moderator: Bob');
    const bobPos = body.indexOf('Bob: thanks');
    expect(alicePos).toBeLessThan(modPos);
    expect(modPos).toBeLessThan(bobPos);
  });

  it('returns 200 text/vtt for format=vtt with a WEBVTT preamble', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSession());
    findSessionForReplayMock.mockResolvedValue(makeSession());
    listParticipantsForSessionMock.mockResolvedValue([]);
    listExecutedModeratorTurnsMock.mockResolvedValue([]);
    iterateAllUtterancesForSessionMock.mockImplementation(
      generatorOf([[makeUtterance({ id: 'u-1' })]]),
    );
    const { GET } = await importRoute();

    const res = await GET(
      new Request(
        'http://localhost/api/sessions/sess-12345678abcdef/exports/transcript?format=vtt',
      ),
      makeContext('sess-12345678abcdef'),
    );

    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('text/vtt; charset=utf-8');
    const cd = res.headers.get('content-disposition');
    expect(cd).toContain('transcript-room-alpha-sess-1234.vtt');

    const body = await res.text();
    expect(body.startsWith('WEBVTT')).toBe(true);
    expect(body).toContain('<v Alice>hi everyone');
  });

  it('falls back to createdAt when actualStart is null so cue clocks start at 0', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSession());
    findSessionForReplayMock.mockResolvedValue(
      makeSession({
        actualStart: null,
        actualEnd: null,
        createdAt: new Date('2026-05-01T09:55:00.000Z'),
      }),
    );
    listParticipantsForSessionMock.mockResolvedValue([]);
    listExecutedModeratorTurnsMock.mockResolvedValue([]);
    iterateAllUtterancesForSessionMock.mockImplementation(
      generatorOf([
        [
          // 5 min after the createdAt fallback → 00:05:00.
          makeUtterance({
            id: 'u-1',
            text: 'first words',
            startTs: new Date('2026-05-01T10:00:00.000Z'),
            endTs: new Date('2026-05-01T10:00:02.000Z'),
          }),
        ],
      ]),
    );
    const { GET } = await importRoute();

    const res = await GET(
      new Request(
        'http://localhost/api/sessions/sess-12345678abcdef/exports/transcript?format=txt',
      ),
      makeContext('sess-12345678abcdef'),
    );

    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain('[00:05:00] Alice: first words');
  });

  it('sanitises filename so room names with slashes or quotes become safe', async () => {
    authMock.mockResolvedValue({ user: { id: 'user-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSession());
    findSessionForReplayMock.mockResolvedValue(
      makeSession({ livekitRoomName: 'room/with spaces & "quotes"' }),
    );
    listParticipantsForSessionMock.mockResolvedValue([]);
    listExecutedModeratorTurnsMock.mockResolvedValue([]);
    iterateAllUtterancesForSessionMock.mockImplementation(generatorOf([[]]));
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-12345678abcdef/exports/transcript'),
      makeContext('sess-12345678abcdef'),
    );

    const cd = res.headers.get('content-disposition');
    expect(cd).toBe('attachment; filename="transcript-room-with-spaces----quotes--sess-1234.txt"');
    // Filename portion (between the quotes) must not carry path /
    // shell-unsafe glyphs that survived sanitisation.
    const match = /filename="([^"]+)"/.exec(cd ?? '');
    const filename = match?.[1] ?? '';
    expect(filename).not.toMatch(/[/&"]/);
  });
});
