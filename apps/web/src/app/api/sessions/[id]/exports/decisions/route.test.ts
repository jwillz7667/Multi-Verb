/**
 * Boundary tests for the decision-log export route.
 *
 * Pins:
 *   - Unauthenticated → 401, no DB calls.
 *   - Unknown session → 404.
 *   - 200 with text/csv content-type and a download-safe filename.
 *   - Header row is the first line of the body.
 *   - Stay-silent and executed decisions both appear (audit invariant
 *     from brief §2.2 — silence is auditable too).
 *   - Target participant name comes from the joined participants list.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { DecisionRow, ParticipantRow, ReplaySessionRow } from '@/features/sessions';

interface MockSession {
  user?: { email: string } | null;
}

const authMock = vi.fn<() => Promise<MockSession | null>>();
const findSessionForReplayMock = vi.fn<(id: string) => Promise<ReplaySessionRow | null>>();
const listParticipantsForSessionMock = vi.fn<(id: string) => Promise<ParticipantRow[]>>();
const iterateAllDecisionsForSessionMock =
  vi.fn<(id: string) => AsyncGenerator<DecisionRow[], void, void>>();

vi.mock('@/lib/auth', () => ({
  auth: (): Promise<MockSession | null> => authMock(),
}));

vi.mock('@/features/sessions', () => ({
  findSessionForReplay: findSessionForReplayMock,
  listParticipantsForSession: listParticipantsForSessionMock,
  iterateAllDecisionsForSession: iterateAllDecisionsForSessionMock,
}));

async function importRoute() {
  return import('./route');
}

function makeContext(id: string) {
  return { params: Promise.resolve({ id }) };
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

function makeDecision(overrides: Partial<DecisionRow>): DecisionRow {
  return {
    id: 'd-default',
    sessionId: 'sess-12345678abcdef',
    tickId: 0n,
    ts: new Date('2026-05-01T10:00:10.000Z'),
    action: 'stay_silent',
    targetParticipantId: null,
    source: 'rules_engine',
    triggeringRule: null,
    researcherId: null,
    researcherHint: null,
    reasonCodes: [],
    reasonHuman: '',
    confidence: null,
    suppressedBy: [],
    wasExecuted: false,
    llmPrompt: null,
    llmOutput: null,
    ttsAudioUrl: null,
    spokenAt: null,
    // cooldown_until is non-nullable in the schema; stay_silent rows
    // set it to `ts` so the resolver's cooldown filter is consistent.
    cooldownUntil: new Date('2026-05-01T10:00:10.000Z'),
    ...overrides,
  };
}

function generatorOf<T>(chunks: T[][]): () => AsyncGenerator<T[], void, void> {
  return async function* () {
    // No-op await keeps the eslint `require-await` rule happy without
    // changing observable behavior — production yields after the real
    // Prisma cursor page resolves.
    await Promise.resolve();
    for (const chunk of chunks) {
      yield chunk;
    }
  };
}

describe('GET /api/sessions/[id]/exports/decisions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 for an unauthenticated request and skips the DB lookups', async () => {
    authMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/exports/decisions'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(401);
    expect(findSessionForReplayMock).not.toHaveBeenCalled();
    expect(iterateAllDecisionsForSessionMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the session is unknown', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionForReplayMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/missing/exports/decisions'),
      makeContext('missing'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_not_found');
    expect(iterateAllDecisionsForSessionMock).not.toHaveBeenCalled();
  });

  it('returns 200 text/csv with a downloadable filename and the column header on the first line', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionForReplayMock.mockResolvedValue(makeSession());
    listParticipantsForSessionMock.mockResolvedValue([
      makeParticipant({ id: 'p-alice', displayName: 'Alice' }),
    ]);
    iterateAllDecisionsForSessionMock.mockImplementation(generatorOf([[makeDecision({})]]));

    const { GET } = await importRoute();
    const res = await GET(
      new Request('http://localhost/api/sessions/sess-12345678abcdef/exports/decisions'),
      makeContext('sess-12345678abcdef'),
    );

    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('text/csv; charset=utf-8; header=present');
    expect(res.headers.get('content-disposition')).toBe(
      'attachment; filename="decisions-room-alpha-sess-1234.csv"',
    );

    const body = await res.text();
    const firstLine = body.split('\n')[0];
    expect(firstLine).toBe(
      'ts,tick_id,action,target_participant_id,target_participant_name,source,triggering_rule,researcher_id,researcher_hint,reason_codes,reason_human,confidence,was_executed,suppressed_by,llm_output,spoken_at,cooldown_until',
    );
  });

  it('includes both stay_silent and executed decisions — silence is auditable too', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionForReplayMock.mockResolvedValue(makeSession());
    listParticipantsForSessionMock.mockResolvedValue([
      makeParticipant({ id: 'p-alice', displayName: 'Alice' }),
    ]);
    iterateAllDecisionsForSessionMock.mockImplementation(
      generatorOf([
        [
          makeDecision({ id: 'd-quiet', action: 'stay_silent', tickId: 1n }),
          makeDecision({
            id: 'd-spoken',
            action: 'prompt',
            tickId: 5n,
            targetParticipantId: 'p-alice',
            wasExecuted: true,
            llmOutput: 'Alice, your take?',
            spokenAt: new Date('2026-05-01T10:00:12.500Z'),
            ts: new Date('2026-05-01T10:00:12.000Z'),
            triggeringRule: 'unheard_participant',
            reasonCodes: ['unheard_60s'],
            confidence: 0.81,
            cooldownUntil: new Date('2026-05-01T10:00:42.000Z'),
          }),
        ],
      ]),
    );

    const { GET } = await importRoute();
    const res = await GET(
      new Request('http://localhost/api/sessions/sess-12345678abcdef/exports/decisions'),
      makeContext('sess-12345678abcdef'),
    );

    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain('stay_silent');
    expect(body).toContain('prompt');
    expect(body).toContain('unheard_participant');
    expect(body).toContain('Alice');
    expect(body).toContain('"Alice, your take?"');
  });

  it('sanitises filename so room names with slashes or quotes become safe', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionForReplayMock.mockResolvedValue(
      makeSession({ livekitRoomName: 'room/with spaces & "quotes"' }),
    );
    listParticipantsForSessionMock.mockResolvedValue([]);
    iterateAllDecisionsForSessionMock.mockImplementation(generatorOf([[]]));

    const { GET } = await importRoute();
    const res = await GET(
      new Request('http://localhost/api/sessions/sess-12345678abcdef/exports/decisions'),
      makeContext('sess-12345678abcdef'),
    );

    const cd = res.headers.get('content-disposition');
    expect(cd).toBe('attachment; filename="decisions-room-with-spaces----quotes--sess-1234.csv"');
    const match = /filename="([^"]+)"/.exec(cd ?? '');
    const filename = match?.[1] ?? '';
    expect(filename).not.toMatch(/[/&"]/);
  });
});
