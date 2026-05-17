/**
 * Boundary tests for /api/studies/[id].
 *
 * Pins:
 *   - Unauthenticated → 401.
 *   - Cross-tenant or missing → 404 (no distinction; avoids existence leak).
 *   - Invalid PATCH body → 400.
 *   - Happy path → 200 with the row, scoped by derived orgId.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { StudyNotFoundError } from '@/features/studies';
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

// `vi.hoisted` lifts the mock fn declarations alongside `vi.mock` so the
// factory below can reference them without a temporal-dead-zone error.
// Without this, the top-level `import { StudyNotFoundError }` triggers
// the mock factory before `const ... = vi.fn()` initializes.
const mocks = vi.hoisted(() => ({
  getStudyMock: vi.fn<(id: string, orgId: string) => Promise<FakeStudy | null>>(),
  updateExistingStudyMock: vi.fn<(arg: unknown) => Promise<FakeStudy>>(),
  authMock: vi.fn<() => Promise<MockSession | null>>(),
}));
const { getStudyMock, updateExistingStudyMock, authMock } = mocks;

vi.mock('@/lib/auth', () => ({
  auth: (): Promise<MockSession | null> => mocks.authMock(),
}));

vi.mock('@/features/studies', async () => {
  const actual = await vi.importActual<typeof StudiesModule>('@/features/studies');
  return {
    ...actual,
    getStudy: mocks.getStudyMock,
    updateExistingStudy: mocks.updateExistingStudyMock,
  };
});

async function importRoute() {
  return import('./route');
}

function makeContext(id: string) {
  return { params: Promise.resolve({ id }) };
}

function fakeStudy(): FakeStudy {
  return {
    id: 'study-1',
    orgId: 'org-uuid',
    name: 'Pilot',
    prompt: 'prompt body',
    rulesVersion: 'v1.0',
    moderatorPersona: {
      style_prompt: 'Be neutral.',
      tone: 'warm',
      formality: 'neutral',
      voice_provider: 'cartesia',
      voice_id: '156fb8d2-335b-4950-9cb3-a2d33befec77',
    },
    createdAt: new Date('2026-05-16T12:00:00Z'),
    createdBy: 'user-uuid',
  };
}

describe('GET /api/studies/[id]', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 when unauthenticated', async () => {
    authMock.mockResolvedValue(null);
    const { GET } = await importRoute();
    const res = await GET(
      new Request('http://localhost/api/studies/study-1'),
      makeContext('study-1'),
    );
    expect(res.status).toBe(401);
    expect(getStudyMock).not.toHaveBeenCalled();
  });

  it('returns 404 when the study is missing or belongs to another org', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    getStudyMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/studies/missing'),
      makeContext('missing'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('study_not_found');
  });

  it('returns 200 with the study on the happy path', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    getStudyMock.mockResolvedValue(fakeStudy());
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/studies/study-1'),
      makeContext('study-1'),
    );

    expect(res.status).toBe(200);
    const body = (await res.json()) as { id: string };
    expect(body.id).toBe('study-1');
  });
});

describe('PATCH /api/studies/[id]', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 when unauthenticated', async () => {
    authMock.mockResolvedValue(null);
    const { PATCH } = await importRoute();

    const res = await PATCH(
      new Request('http://localhost/api/studies/study-1', {
        method: 'PATCH',
        body: JSON.stringify({ name: 'Renamed' }),
      }),
      makeContext('study-1'),
    );

    expect(res.status).toBe(401);
    expect(updateExistingStudyMock).not.toHaveBeenCalled();
  });

  it('returns 400 when the body fails Zod validation', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    const { PATCH } = await importRoute();

    const res = await PATCH(
      new Request('http://localhost/api/studies/study-1', {
        method: 'PATCH',
        body: JSON.stringify({ name: '' }),
      }),
      makeContext('study-1'),
    );

    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('invalid_input');
  });

  it('returns 404 when the study is not found (cross-tenant smuggling guard)', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    updateExistingStudyMock.mockRejectedValue(new StudyNotFoundError('study-1'));
    const { PATCH } = await importRoute();

    const res = await PATCH(
      new Request('http://localhost/api/studies/study-1', {
        method: 'PATCH',
        body: JSON.stringify({ name: 'Renamed' }),
      }),
      makeContext('study-1'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('study_not_found');
  });

  it('returns 200 with the updated study on the happy path', async () => {
    authMock.mockResolvedValue({ user: { id: 'u-1', email: 'r@example.com' } });
    const updated = { ...fakeStudy(), name: 'Renamed' };
    updateExistingStudyMock.mockResolvedValue(updated);
    const { PATCH } = await importRoute();

    const res = await PATCH(
      new Request('http://localhost/api/studies/study-1', {
        method: 'PATCH',
        body: JSON.stringify({ name: 'Renamed' }),
      }),
      makeContext('study-1'),
    );

    expect(res.status).toBe(200);
    const body = (await res.json()) as { name: string };
    expect(body.name).toBe('Renamed');

    const cmd = updateExistingStudyMock.mock.calls[0]?.[0] as {
      studyId: string;
      orgId: string;
      patch: { name?: string };
    };
    expect(cmd.studyId).toBe('study-1');
    expect(cmd.orgId).toMatch(/^[0-9a-f-]{36}$/u);
    expect(cmd.patch.name).toBe('Renamed');
  });
});
