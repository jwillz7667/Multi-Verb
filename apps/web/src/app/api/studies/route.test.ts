/**
 * Boundary tests for /api/studies.
 *
 * Pins:
 *   - Unauthenticated → 401.
 *   - Invalid body → 400 with Zod issues.
 *   - Happy path → 201 with the created study + 200 list scoped to the
 *     caller's derived orgId.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type * as StudiesModule from '@/features/studies';

interface MockSession {
  user?: { id: string; email: string } | null;
}

interface FakeStudy {
  id: string;
  orgId: string;
  name: string;
  prompt: string;
  rulesVersion: string;
  moderatorPersona: Record<string, unknown>;
  createdAt: Date;
  createdBy: string;
}

const createNewStudyMock = vi.fn<(arg: unknown) => Promise<FakeStudy>>();
const listStudiesMock = vi.fn<(orgId: string) => Promise<FakeStudy[]>>();
const authMock = vi.fn<() => Promise<MockSession | null>>();

vi.mock('@/lib/auth', () => ({
  auth: (): Promise<MockSession | null> => authMock(),
}));

vi.mock('@/features/studies', async () => {
  const actual = await vi.importActual<typeof StudiesModule>('@/features/studies');
  return {
    ...actual,
    createNewStudy: createNewStudyMock,
    listStudies: listStudiesMock,
  };
});

async function importRoute() {
  return import('./route');
}

const VALID_CREATE_BODY = {
  name: 'Pilot',
  prompt: 'How do you currently manage subscriptions?',
  moderatorPersona: {
    style_prompt: 'You are a careful moderator.',
    voice_id: '156fb8d2-335b-4950-9cb3-a2d33befec77',
  },
};

function fakeStudy(overrides: Partial<FakeStudy> = {}): FakeStudy {
  return {
    id: 'study-1',
    orgId: 'org-1',
    name: VALID_CREATE_BODY.name,
    prompt: VALID_CREATE_BODY.prompt,
    rulesVersion: 'v1.0',
    moderatorPersona: { ...VALID_CREATE_BODY.moderatorPersona, tone: 'warm' },
    createdAt: new Date('2026-05-16T12:00:00Z'),
    createdBy: 'user-uuid-1',
    ...overrides,
  };
}

describe('POST /api/studies', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 for an unauthenticated request', async () => {
    authMock.mockResolvedValue(null);
    const { POST } = await importRoute();

    const res = await POST(
      new Request('http://localhost/api/studies', {
        method: 'POST',
        body: JSON.stringify(VALID_CREATE_BODY),
      }),
    );

    expect(res.status).toBe(401);
    expect(createNewStudyMock).not.toHaveBeenCalled();
  });

  it('returns 400 when the body fails Zod validation', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    const { POST } = await importRoute();

    const res = await POST(
      new Request('http://localhost/api/studies', {
        method: 'POST',
        body: JSON.stringify({ name: '', prompt: '', moderatorPersona: {} }),
      }),
    );

    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: string; issues: unknown[] };
    expect(body.error).toBe('invalid_input');
    expect(Array.isArray(body.issues)).toBe(true);
    expect(createNewStudyMock).not.toHaveBeenCalled();
  });

  it('returns 201 and the created study on the happy path', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    createNewStudyMock.mockResolvedValue(fakeStudy());
    const { POST } = await importRoute();

    const res = await POST(
      new Request('http://localhost/api/studies', {
        method: 'POST',
        body: JSON.stringify(VALID_CREATE_BODY),
      }),
    );

    expect(res.status).toBe(201);
    const body = (await res.json()) as { id: string; name: string };
    expect(body.id).toBe('study-1');
    expect(body.name).toBe('Pilot');

    expect(createNewStudyMock).toHaveBeenCalledTimes(1);
    const cmd = createNewStudyMock.mock.calls[0]?.[0] as {
      input: { name: string };
      orgId: string;
      createdBy: string;
    };
    // orgId + createdBy must be derived from the session user id and
    // never blindly read from the request body.
    expect(cmd.input.name).toBe('Pilot');
    expect(cmd.orgId).toMatch(/^[0-9a-f-]{36}$/u);
    expect(cmd.createdBy).toMatch(/^[0-9a-f-]{36}$/u);
    expect(cmd.orgId).not.toBe(cmd.createdBy);
  });

  it('tolerates an unparseable body and returns 400 (does not throw)', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    const { POST } = await importRoute();

    const res = await POST(
      new Request('http://localhost/api/studies', {
        method: 'POST',
        body: 'not-json',
      }),
    );

    expect(res.status).toBe(400);
    expect(createNewStudyMock).not.toHaveBeenCalled();
  });
});

describe('GET /api/studies', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 for an unauthenticated request', async () => {
    authMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET();

    expect(res.status).toBe(401);
    expect(listStudiesMock).not.toHaveBeenCalled();
  });

  it('returns the org-scoped study list', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    listStudiesMock.mockResolvedValue([fakeStudy(), fakeStudy({ id: 'study-2', name: 'Other' })]);
    const { GET } = await importRoute();

    const res = await GET();

    expect(res.status).toBe(200);
    const body = (await res.json()) as { studies: { id: string }[] };
    expect(body.studies).toHaveLength(2);
    expect(body.studies[0]?.id).toBe('study-1');

    const orgId = listStudiesMock.mock.calls[0]?.[0];
    expect(orgId).toMatch(/^[0-9a-f-]{36}$/u);
  });
});
