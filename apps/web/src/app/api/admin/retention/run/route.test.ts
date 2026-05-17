/**
 * Boundary tests for the daily retention cron route.
 *
 * Pins the auth surface (the part attackers see) without spinning up
 * Prisma or R2 — the runner module is mocked, so this file only proves:
 *   - 503 when CRON_SECRET is unset (deployment misconfig — Vercel
 *     should retry rather than permanently shelve as 4xx),
 *   - 401 for missing / wrong bearer token (never invokes the runner),
 *   - 200 + JSON summary on the happy path, including the per-session
 *     `errors` array passthrough.
 *
 * `vi.hoisted` + a `serverEnv` getter is the same pattern the R2 tests
 * use; it lets each `it` flip `CRON_SECRET` without re-importing the
 * route module.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { PerSessionError, RetentionRunResult } from '@/features/retention';

interface MockServerEnv {
  CRON_SECRET?: string | undefined;
}

const mocks = vi.hoisted(() => ({
  serverEnvHolder: { current: null as MockServerEnv | null },
  runRetentionMock: vi.fn<() => Promise<RetentionRunResult>>(),
}));

vi.mock('@/lib/env', () => ({
  get serverEnv() {
    return mocks.serverEnvHolder.current;
  },
  get env() {
    return mocks.serverEnvHolder.current ?? {};
  },
  get clientEnv() {
    return {};
  },
}));

vi.mock('@/features/retention', () => ({
  createPrismaRetentionRepo: vi.fn(() => ({})),
  defaultR2KeyDeleter: { deleteKeys: vi.fn() },
  runRetention: (..._args: unknown[]): Promise<RetentionRunResult> => mocks.runRetentionMock(),
}));

const { serverEnvHolder, runRetentionMock } = mocks;

async function importRoute() {
  return import('./route');
}

function makeResult(overrides: Partial<RetentionRunResult> = {}): RetentionRunResult {
  return {
    studiesProcessed: 0,
    sessionsProcessed: 0,
    sessionsSkipped: 0,
    snapshotsDeleted: 0,
    recordingsDeleted: 0,
    transcriptsDeleted: 0,
    errors: [],
    ...overrides,
  };
}

function makeRequest(headers: Record<string, string> = {}): Request {
  return new Request('http://localhost/api/admin/retention/run', {
    method: 'POST',
    headers,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  serverEnvHolder.current = { CRON_SECRET: 'secret-token-for-tests' };
  runRetentionMock.mockResolvedValue(makeResult());
});

describe('POST /api/admin/retention/run', () => {
  it('returns 503 when CRON_SECRET is unset and never invokes the runner', async () => {
    serverEnvHolder.current = { CRON_SECRET: undefined };
    const { POST } = await importRoute();

    const res = await POST(makeRequest({ authorization: 'Bearer secret-token-for-tests' }));

    expect(res.status).toBe(503);
    await expect(res.json()).resolves.toEqual({ error: 'cron_secret_not_configured' });
    expect(runRetentionMock).not.toHaveBeenCalled();
  });

  it('returns 503 when CRON_SECRET is the empty string', async () => {
    serverEnvHolder.current = { CRON_SECRET: '' };
    const { POST } = await importRoute();

    const res = await POST(makeRequest({ authorization: 'Bearer secret-token-for-tests' }));

    expect(res.status).toBe(503);
    expect(runRetentionMock).not.toHaveBeenCalled();
  });

  it('returns 401 when the Authorization header is missing', async () => {
    const { POST } = await importRoute();

    const res = await POST(makeRequest());

    expect(res.status).toBe(401);
    await expect(res.json()).resolves.toEqual({ error: 'unauthorized' });
    expect(runRetentionMock).not.toHaveBeenCalled();
  });

  it('returns 401 when the Bearer token does not match CRON_SECRET', async () => {
    const { POST } = await importRoute();

    const res = await POST(makeRequest({ authorization: 'Bearer wrong-token' }));

    expect(res.status).toBe(401);
    expect(runRetentionMock).not.toHaveBeenCalled();
  });

  it('returns 401 when the header is present but lacks the Bearer scheme', async () => {
    const { POST } = await importRoute();

    const res = await POST(makeRequest({ authorization: 'secret-token-for-tests' }));

    expect(res.status).toBe(401);
    expect(runRetentionMock).not.toHaveBeenCalled();
  });

  it('returns 200 with the summary echoed from the runner on the happy path', async () => {
    runRetentionMock.mockResolvedValue(
      makeResult({
        studiesProcessed: 3,
        sessionsProcessed: 7,
        sessionsSkipped: 2,
        snapshotsDeleted: 1_440,
        recordingsDeleted: 5,
        transcriptsDeleted: 980,
      }),
    );
    const { POST } = await importRoute();

    const res = await POST(makeRequest({ authorization: 'Bearer secret-token-for-tests' }));

    expect(res.status).toBe(200);
    expect(runRetentionMock).toHaveBeenCalledOnce();
    const body = (await res.json()) as {
      ok: boolean;
      summary: Record<string, number>;
      errors: PerSessionError[];
    };
    expect(body.ok).toBe(true);
    expect(body.summary).toEqual({
      studiesProcessed: 3,
      sessionsProcessed: 7,
      sessionsSkipped: 2,
      snapshotsDeleted: 1_440,
      recordingsDeleted: 5,
      transcriptsDeleted: 980,
      errorCount: 0,
    });
    expect(body.errors).toEqual([]);
  });

  it('returns 200 and surfaces per-session errors verbatim for ops visibility', async () => {
    const failures: PerSessionError[] = [
      { sessionId: 'sess-aaaa', studyId: 'study-1', message: 'r2 timeout' },
      { sessionId: 'sess-bbbb', studyId: null, message: 'prisma deadlock' },
    ];
    runRetentionMock.mockResolvedValue(
      makeResult({
        studiesProcessed: 1,
        sessionsProcessed: 5,
        errors: failures,
      }),
    );
    const { POST } = await importRoute();

    const res = await POST(makeRequest({ authorization: 'Bearer secret-token-for-tests' }));

    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      summary: { errorCount: number };
      errors: PerSessionError[];
    };
    expect(body.summary.errorCount).toBe(2);
    expect(body.errors).toEqual(failures);
  });
});
