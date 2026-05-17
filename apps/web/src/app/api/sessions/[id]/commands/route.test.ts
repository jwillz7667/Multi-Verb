/**
 * Boundary tests for the researcher command POST route.
 *
 * Covers auth, JSON parsing, Zod validation across the discriminated
 * union, session-state guards (404/409), and the happy-path delegation
 * to `publishResearcherCommand`. A live Redis is exercised by the
 * Phase 5 integration suite; these tests pin only the route contract.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

interface MockSession {
  user?: { id?: string; email?: string } | null;
}

interface FakePublishResult {
  commandId: string;
  issuedAt: string;
  streamEntryId: string;
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
const publishMock =
  vi.fn<
    (args: {
      sessionId: string;
      researcherId: string;
      input: unknown;
    }) => Promise<FakePublishResult>
  >();
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

vi.mock('@/features/sessions', async () => {
  const real = await import('@/features/sessions/commands');
  return {
    publishResearcherCommand: publishMock,
    PublishCommandInputSchema: real.PublishCommandInputSchema,
    SessionNotFoundError: FakeSessionNotFoundError,
    SessionAlreadyEndedError: FakeSessionAlreadyEndedError,
  };
});

vi.mock('@/lib/scoped-db', () => ({
  scopedDb: (_orgId: string) => ({
    sessions: {
      findById: scopedSessionsFindByIdMock,
    },
  }),
}));

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

async function importRoute() {
  return import('./route');
}

function makeContext(id: string) {
  return { params: Promise.resolve({ id }) };
}

function postRequest(url: string, body: unknown, init?: { rawBody?: string }) {
  return new Request(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: init?.rawBody ?? JSON.stringify(body),
  });
}

describe('POST /api/sessions/[id]/commands', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 for an unauthenticated request', async () => {
    authMock.mockResolvedValue(null);
    const { POST } = await importRoute();

    const res = await POST(
      postRequest('http://localhost/api/sessions/abc/commands', {
        command_type: 'mute_moderator',
      }),
      makeContext('abc'),
    );

    expect(res.status).toBe(401);
    expect(publishMock).not.toHaveBeenCalled();
    expect(scopedSessionsFindByIdMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the session belongs to another org (scopedDb gate rejects before publish)', async () => {
    // Cross-org caller mustn't be able to mute / pause / end / whisper
    // another tenant's live focus group. Without the gate, `publishMock`
    // would XADD the command onto the engine's stream before any
    // ownership check — the moderator would actually execute it.
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(null);
    const { POST } = await importRoute();

    const res = await POST(
      postRequest('http://localhost/api/sessions/sess-other/commands', {
        command_type: 'end_session',
      }),
      makeContext('sess-other'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_not_found');
    expect(publishMock).not.toHaveBeenCalled();
  });

  it('returns 401 when the session has no user id', async () => {
    // Auth provider returning a session without `user.id` is a misconfig;
    // we don't want to mint `researcher_actions` rows with an empty FK.
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    const { POST } = await importRoute();

    const res = await POST(
      postRequest('http://localhost/api/sessions/abc/commands', {
        command_type: 'mute_moderator',
      }),
      makeContext('abc'),
    );

    expect(res.status).toBe(401);
    expect(publishMock).not.toHaveBeenCalled();
  });

  it('returns 400 for invalid JSON', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    const { POST } = await importRoute();

    const res = await POST(
      postRequest('http://localhost/api/sessions/abc/commands', {}, { rawBody: '{not json' }),
      makeContext('abc'),
    );

    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('invalid_json');
    expect(publishMock).not.toHaveBeenCalled();
  });

  it('returns 422 when command_type is missing', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    const { POST } = await importRoute();

    const res = await POST(
      postRequest('http://localhost/api/sessions/abc/commands', { payload: {} }),
      makeContext('abc'),
    );

    expect(res.status).toBe(422);
    const body = (await res.json()) as { error: string; issues: unknown };
    expect(body.error).toBe('invalid_payload');
    expect(body.issues).toBeDefined();
    expect(publishMock).not.toHaveBeenCalled();
  });

  it('returns 422 when force_prompt payload is missing the prompt field', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    const { POST } = await importRoute();

    const res = await POST(
      postRequest('http://localhost/api/sessions/abc/commands', {
        command_type: 'force_prompt',
        payload: {},
      }),
      makeContext('abc'),
    );

    expect(res.status).toBe(422);
    expect(publishMock).not.toHaveBeenCalled();
  });

  it('returns 422 when set_quietness_budget has no fields set', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    const { POST } = await importRoute();

    const res = await POST(
      postRequest('http://localhost/api/sessions/abc/commands', {
        command_type: 'set_quietness_budget',
        payload: {},
      }),
      makeContext('abc'),
    );

    expect(res.status).toBe(422);
    const body = (await res.json()) as { error: string; issues: { message: string }[] };
    expect(body.error).toBe('invalid_payload');
    // The refinement error message identifies the constraint that failed.
    expect(body.issues.some((i) => i.message.includes('at least one'))).toBe(true);
    expect(publishMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the session is unknown (publish throws SessionNotFoundError)', async () => {
    // Race window: the gate could pass and the row vanish before XADD —
    // the publish layer's `SessionNotFoundError` still gets mapped to 404.
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow('missing'));
    publishMock.mockRejectedValue(new FakeSessionNotFoundError('missing'));
    const { POST } = await importRoute();

    const res = await POST(
      postRequest('http://localhost/api/sessions/missing/commands', {
        command_type: 'mute_moderator',
      }),
      makeContext('missing'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_not_found');
  });

  it('returns 409 when the session has already ended', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow('done'));
    publishMock.mockRejectedValue(new FakeSessionAlreadyEndedError('done'));
    const { POST } = await importRoute();

    const res = await POST(
      postRequest('http://localhost/api/sessions/done/commands', {
        command_type: 'mute_moderator',
      }),
      makeContext('done'),
    );

    expect(res.status).toBe(409);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_already_ended');
  });

  it('publishes a flag_moment command with an empty default payload', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow('sess-1'));
    publishMock.mockResolvedValue({
      commandId: '11111111-1111-4111-8111-111111111111',
      issuedAt: '2026-05-17T10:00:00.000Z',
      streamEntryId: '1700000000000-0',
    });
    const { POST } = await importRoute();

    const res = await POST(
      postRequest('http://localhost/api/sessions/sess-1/commands', {
        command_type: 'flag_moment',
      }),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      ok: boolean;
      command_id: string;
      stream_entry_id: string;
    };
    expect(body.ok).toBe(true);
    expect(body.command_id).toBe('11111111-1111-4111-8111-111111111111');
    expect(body.stream_entry_id).toBe('1700000000000-0');

    expect(publishMock).toHaveBeenCalledTimes(1);
    const call = publishMock.mock.calls[0]![0];
    expect(call.sessionId).toBe('sess-1');
    expect(call.researcherId).toBe('u-1');
    expect(call.input).toEqual({ command_type: 'flag_moment', payload: {} });
  });

  it('publishes a force_prompt with a target participant id', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow('sess-2'));
    publishMock.mockResolvedValue({
      commandId: '22222222-2222-4222-8222-222222222222',
      issuedAt: '2026-05-17T10:00:01.000Z',
      streamEntryId: '1700000000001-0',
    });
    const { POST } = await importRoute();

    const target = '33333333-3333-4333-8333-333333333333';
    const res = await POST(
      postRequest('http://localhost/api/sessions/sess-2/commands', {
        command_type: 'force_prompt',
        payload: {
          prompt: 'ask Maria what she meant by too expensive',
          target_participant_id: target,
        },
      }),
      makeContext('sess-2'),
    );

    expect(res.status).toBe(200);
    expect(publishMock).toHaveBeenCalledTimes(1);
    const call = publishMock.mock.calls[0]![0];
    expect(call.input).toEqual({
      command_type: 'force_prompt',
      payload: {
        prompt: 'ask Maria what she meant by too expensive',
        target_participant_id: target,
      },
    });
  });

  it('publishes a set_quietness_budget with one field set', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    scopedSessionsFindByIdMock.mockResolvedValue(makeScopedSessionRow('sess-3'));
    publishMock.mockResolvedValue({
      commandId: '44444444-4444-4444-8444-444444444444',
      issuedAt: '2026-05-17T10:00:02.000Z',
      streamEntryId: '1700000000002-0',
    });
    const { POST } = await importRoute();

    const res = await POST(
      postRequest('http://localhost/api/sessions/sess-3/commands', {
        command_type: 'set_quietness_budget',
        payload: { max_utterances_per_10min: 3 },
      }),
      makeContext('sess-3'),
    );

    expect(res.status).toBe(200);
    expect(publishMock).toHaveBeenCalledTimes(1);
    const call = publishMock.mock.calls[0]![0];
    expect(call.input).toEqual({
      command_type: 'set_quietness_budget',
      payload: { max_utterances_per_10min: 3 },
    });
  });
});
