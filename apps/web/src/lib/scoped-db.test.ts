/**
 * Unit tests for `scopedDb` — the tenant facade over Prisma.
 *
 * Strategy: inject a fake PrismaClient via `options.prisma` so we can
 * record the exact `where` clause every call site emits, without
 * spinning up Postgres. The point of this helper is that the orgId
 * filter is impossible to forget at the call site — so the tests pin
 * that the filter is always present, in the right shape, on every
 * read.
 *
 * Cross-tenant reads are modeled as "the fake returns null" (the same
 * behavior Postgres would exhibit when the WHERE clause excludes the
 * row). What we're pinning is that the where clause was correctly
 * narrowed; once that holds, real Postgres is doing the rest.
 *
 * `unscopedDbForAdminCron` is checked via a mocked `@/lib/db` module
 * (we need to assert identity with the singleton without instantiating
 * a real PrismaClient).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { PrismaClient } from '@/generated/prisma';

import { scopedDb, unscopedDbForAdminCron } from './scoped-db';

const DB_SENTINEL = { __sentinel: 'fake-db-singleton' } as unknown as PrismaClient;

vi.mock('@/lib/db', () => ({
  get db(): PrismaClient {
    return DB_SENTINEL;
  },
}));

interface PrismaCall {
  args: unknown;
}

interface FakePrisma {
  client: PrismaClient;
  calls: {
    studyFindFirst: PrismaCall[];
    studyFindMany: PrismaCall[];
    sessionFindFirst: PrismaCall[];
    sessionFindMany: PrismaCall[];
  };
  responses: {
    studyFindFirst: unknown;
    studyFindMany: unknown;
    sessionFindFirst: unknown;
    sessionFindMany: unknown;
  };
}

function makeFakePrisma(): FakePrisma {
  const calls: FakePrisma['calls'] = {
    studyFindFirst: [],
    studyFindMany: [],
    sessionFindFirst: [],
    sessionFindMany: [],
  };
  const responses: FakePrisma['responses'] = {
    studyFindFirst: null,
    studyFindMany: [],
    sessionFindFirst: null,
    sessionFindMany: [],
  };
  const client = {
    study: {
      findFirst: (args: unknown): Promise<unknown> => {
        calls.studyFindFirst.push({ args });
        return Promise.resolve(responses.studyFindFirst);
      },
      findMany: (args: unknown): Promise<unknown> => {
        calls.studyFindMany.push({ args });
        return Promise.resolve(responses.studyFindMany);
      },
    },
    moderatedSession: {
      findFirst: (args: unknown): Promise<unknown> => {
        calls.sessionFindFirst.push({ args });
        return Promise.resolve(responses.sessionFindFirst);
      },
      findMany: (args: unknown): Promise<unknown> => {
        calls.sessionFindMany.push({ args });
        return Promise.resolve(responses.sessionFindMany);
      },
    },
  } as unknown as PrismaClient;
  return { client, calls, responses };
}

const ORG_A = '11111111-1111-1111-1111-111111111111';
const ORG_B = '22222222-2222-2222-2222-222222222222';
const STUDY_ID = '33333333-3333-3333-3333-333333333333';
const SESSION_ID = '44444444-4444-4444-4444-444444444444';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('scopedDb(orgId)', () => {
  it('throws fail-fast when orgId is the empty string', () => {
    expect(() => scopedDb('')).toThrow(/orgId must be a non-empty string/u);
  });

  it('throws fail-fast when orgId is not a string', () => {
    // Test the runtime guard — the type system already rejects non-strings,
    // but production callers go through `orgIdForUser(session.user.id)` which
    // is `string | undefined`; we'd rather crash than silently broaden the
    // query to "any row".
    expect(() => scopedDb(undefined as unknown as string)).toThrow(
      /orgId must be a non-empty string/u,
    );
  });
});

describe('scopedDb.studies', () => {
  it('findById injects { id, orgId } into the where clause', async () => {
    const fake = makeFakePrisma();
    fake.responses.studyFindFirst = {
      id: STUDY_ID,
      orgId: ORG_A,
      name: 'Onboarding flow',
      createdAt: new Date('2026-05-10T00:00:00.000Z'),
    };

    const result = await scopedDb(ORG_A, { prisma: fake.client }).studies.findById(STUDY_ID);

    expect(fake.calls.studyFindFirst).toHaveLength(1);
    const args = fake.calls.studyFindFirst[0]?.args as { where: unknown; select: unknown };
    expect(args.where).toEqual({ id: STUDY_ID, orgId: ORG_A });
    // The projection guards against a future Prisma row leaking unintended
    // columns (prompt_embedding, rules_config, etc.) to consumers.
    expect(args.select).toEqual({ id: true, orgId: true, name: true, createdAt: true });
    expect(result).toEqual({
      id: STUDY_ID,
      orgId: ORG_A,
      name: 'Onboarding flow',
      createdAt: new Date('2026-05-10T00:00:00.000Z'),
    });
  });

  it('findById returns null when the study belongs to another org', async () => {
    const fake = makeFakePrisma();
    // Cross-org case: Postgres would exclude the row via the WHERE clause,
    // so findFirst sees nothing.
    fake.responses.studyFindFirst = null;

    const result = await scopedDb(ORG_A, { prisma: fake.client }).studies.findById(STUDY_ID);

    expect(result).toBeNull();
    // Pin: even on the negative path, the orgId filter must be in the query
    // — otherwise we'd silently return a row from the wrong org.
    const args = fake.calls.studyFindFirst[0]?.args as { where: { orgId: string } };
    expect(args.where.orgId).toBe(ORG_A);
  });

  it('uses different orgId per facade — same prisma client, no cross-bleed', async () => {
    const fake = makeFakePrisma();
    fake.responses.studyFindFirst = null;

    const scopedA = scopedDb(ORG_A, { prisma: fake.client });
    const scopedB = scopedDb(ORG_B, { prisma: fake.client });

    await scopedA.studies.findById(STUDY_ID);
    await scopedB.studies.findById(STUDY_ID);

    expect(fake.calls.studyFindFirst).toHaveLength(2);
    const argsA = fake.calls.studyFindFirst[0]?.args as { where: { orgId: string } };
    const argsB = fake.calls.studyFindFirst[1]?.args as { where: { orgId: string } };
    expect(argsA.where.orgId).toBe(ORG_A);
    expect(argsB.where.orgId).toBe(ORG_B);
  });

  it('list injects { orgId }, paginates with createdAt+id ordering, and respects the limit', async () => {
    const fake = makeFakePrisma();
    fake.responses.studyFindMany = [
      {
        id: STUDY_ID,
        orgId: ORG_A,
        name: 'A',
        createdAt: new Date('2026-05-10T00:00:00.000Z'),
      },
    ];

    const rows = await scopedDb(ORG_A, { prisma: fake.client }).studies.list(25);

    expect(fake.calls.studyFindMany).toHaveLength(1);
    const args = fake.calls.studyFindMany[0]?.args as {
      where: unknown;
      orderBy: unknown;
      take: number;
      select: unknown;
    };
    expect(args.where).toEqual({ orgId: ORG_A });
    // Stable ordering: createdAt desc with id desc as tiebreaker. Required
    // for cursor pagination in future levels.
    expect(args.orderBy).toEqual([{ createdAt: 'desc' }, { id: 'desc' }]);
    expect(args.take).toBe(25);
    expect(rows).toHaveLength(1);
  });

  it('list defaults to a 50-row limit when none is passed', async () => {
    const fake = makeFakePrisma();
    fake.responses.studyFindMany = [];

    await scopedDb(ORG_A, { prisma: fake.client }).studies.list();

    const args = fake.calls.studyFindMany[0]?.args as { take: number };
    expect(args.take).toBe(50);
  });
});

describe('scopedDb.sessions', () => {
  const SESSION_ROW = {
    id: SESSION_ID,
    studyId: STUDY_ID,
    livekitRoomName: 'verbio-room-abc',
    status: 'scheduled',
    scheduledStart: new Date('2026-05-20T15:00:00.000Z'),
    actualStart: null,
    actualEnd: null,
    createdAt: new Date('2026-05-15T00:00:00.000Z'),
  };

  it('findById scopes through study.orgId — sessions are tenant-scoped via their parent study', async () => {
    const fake = makeFakePrisma();
    fake.responses.sessionFindFirst = SESSION_ROW;

    const result = await scopedDb(ORG_A, { prisma: fake.client }).sessions.findById(SESSION_ID);

    expect(fake.calls.sessionFindFirst).toHaveLength(1);
    const args = fake.calls.sessionFindFirst[0]?.args as { where: unknown };
    // The nested where is the load-bearing part — a session with no study,
    // or a session whose study belongs to another org, must be excluded
    // by Postgres before findFirst returns.
    expect(args.where).toEqual({ id: SESSION_ID, study: { orgId: ORG_A } });
    expect(result).toEqual({
      id: SESSION_ID,
      studyId: STUDY_ID,
      livekitRoomName: 'verbio-room-abc',
      status: 'scheduled',
      scheduledStart: new Date('2026-05-20T15:00:00.000Z'),
      actualStart: null,
      actualEnd: null,
      createdAt: new Date('2026-05-15T00:00:00.000Z'),
    });
  });

  it('findById returns null when the session belongs to another org', async () => {
    const fake = makeFakePrisma();
    fake.responses.sessionFindFirst = null;

    const result = await scopedDb(ORG_A, { prisma: fake.client }).sessions.findById(SESSION_ID);

    expect(result).toBeNull();
  });

  it('findById returns null when row.studyId is null (orphaned Phase 2 row)', async () => {
    // Defense in depth: even if the WHERE somehow let a null-study row
    // through (shouldn't happen — `study: { orgId }` requires a study),
    // the projection check at the caller still drops it. This pin makes
    // sure that secondary guard exists.
    const fake = makeFakePrisma();
    fake.responses.sessionFindFirst = { ...SESSION_ROW, studyId: null };

    const result = await scopedDb(ORG_A, { prisma: fake.client }).sessions.findById(SESSION_ID);

    expect(result).toBeNull();
  });

  it('findByLivekitRoomName scopes through study.orgId — the same tenant guard as findById', async () => {
    const fake = makeFakePrisma();
    fake.responses.sessionFindFirst = SESSION_ROW;

    const result = await scopedDb(ORG_A, {
      prisma: fake.client,
    }).sessions.findByLivekitRoomName('verbio-room-abc');

    const args = fake.calls.sessionFindFirst[0]?.args as { where: unknown };
    expect(args.where).toEqual({
      livekitRoomName: 'verbio-room-abc',
      study: { orgId: ORG_A },
    });
    expect(result?.livekitRoomName).toBe('verbio-room-abc');
  });

  it('findByLivekitRoomName returns null when row.studyId is null', async () => {
    const fake = makeFakePrisma();
    fake.responses.sessionFindFirst = { ...SESSION_ROW, studyId: null };

    const result = await scopedDb(ORG_A, {
      prisma: fake.client,
    }).sessions.findByLivekitRoomName('verbio-room-abc');

    expect(result).toBeNull();
  });

  it('listRecent injects { study: { orgId } }, orders newest first, and respects the limit', async () => {
    const fake = makeFakePrisma();
    fake.responses.sessionFindMany = [
      SESSION_ROW,
      {
        ...SESSION_ROW,
        id: '55555555-5555-5555-5555-555555555555',
        livekitRoomName: 'verbio-room-def',
        createdAt: new Date('2026-05-14T00:00:00.000Z'),
      },
    ];

    const rows = await scopedDb(ORG_A, { prisma: fake.client }).sessions.listRecent(25);

    expect(fake.calls.sessionFindMany).toHaveLength(1);
    const args = fake.calls.sessionFindMany[0]?.args as {
      where: unknown;
      orderBy: unknown;
      take: number;
      select: unknown;
    };
    // Nested where forces the join through `study.orgId` — sessions with
    // no study (legacy Phase 1 rows) are excluded by Postgres.
    expect(args.where).toEqual({ study: { orgId: ORG_A } });
    expect(args.orderBy).toEqual([{ createdAt: 'desc' }, { id: 'desc' }]);
    expect(args.take).toBe(25);
    expect(rows).toHaveLength(2);
    expect(rows[0]?.livekitRoomName).toBe('verbio-room-abc');
  });

  it('listRecent defaults to a 25-row limit when none is passed', async () => {
    const fake = makeFakePrisma();
    fake.responses.sessionFindMany = [];

    await scopedDb(ORG_A, { prisma: fake.client }).sessions.listRecent();

    const args = fake.calls.sessionFindMany[0]?.args as { take: number };
    expect(args.take).toBe(25);
  });

  it('listRecent filters out rows where studyId is null (defense in depth)', async () => {
    // The WHERE clause already excludes orphans through the `study: { orgId }`
    // join, but the post-map guard catches any future Prisma quirk that
    // could let one slip — e.g., a left-join introduced via includes.
    const fake = makeFakePrisma();
    fake.responses.sessionFindMany = [SESSION_ROW, { ...SESSION_ROW, id: 'orphan', studyId: null }];

    const rows = await scopedDb(ORG_A, { prisma: fake.client }).sessions.listRecent();

    expect(rows).toHaveLength(1);
    expect(rows[0]?.id).toBe(SESSION_ID);
  });
});

describe('unscopedDbForAdminCron(reason)', () => {
  it('returns the shared db singleton when given a justification string', () => {
    const client = unscopedDbForAdminCron('daily retention sweep');
    expect(client).toBe(DB_SENTINEL);
  });

  it('returns the same singleton on every call — no per-invocation client creation', () => {
    const a = unscopedDbForAdminCron('retention cron');
    const b = unscopedDbForAdminCron('livekit webhook');
    expect(a).toBe(b);
  });

  it('throws when the reason is the empty string', () => {
    expect(() => unscopedDbForAdminCron('')).toThrow(/justification string is required/u);
  });

  it('throws when the reason is not a string', () => {
    expect(() => unscopedDbForAdminCron(undefined as unknown as string)).toThrow(
      /justification string is required/u,
    );
  });
});
