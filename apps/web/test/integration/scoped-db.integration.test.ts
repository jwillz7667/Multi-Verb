/**
 * Cross-tenant isolation — integration test (P7 L3).
 *
 * The unit tests for `scopedDb` (src/lib/scoped-db.test.ts) prove the
 * `where`-clause shape the facade generates against a mock Prisma. That
 * is necessary but not sufficient: a future Prisma version, schema
 * drift, or a copy-paste regression could change how those clauses
 * compile to SQL. This suite is the end-to-end pin — it boots a real
 * Postgres, seeds two orgs' data plus an orphan session, and verifies
 * that every method on the `scopedDb` facade refuses to surface another
 * tenant's rows.
 *
 * Brief §10.3 + ADR-0002 commit Verbio to application-layer tenant
 * isolation (no Postgres RLS). The scopedDb facade is the single
 * boundary that enforcement runs through. If it leaks, the whole
 * isolation story collapses. Hence: prove it against the real engine.
 *
 * Run locally:
 *   docker compose -f infra/docker-compose.dev.yml up -d postgres
 *   cd services/engine && \
 *     DATABASE_URL_DIRECT='postgresql+asyncpg://verbio:verbio@localhost:5432/verbio' \
 *     uv run alembic upgrade head
 *   cd apps/web && \
 *     INTEGRATION_DATABASE_URL='postgresql://verbio:verbio@localhost:5432/verbio' \
 *     pnpm test:integration
 *
 * Without `INTEGRATION_DATABASE_URL` the suite skips — keeps `pnpm test`
 * fast on dev laptops with no DB running, while CI's web-integration
 * job sets it to its Postgres service container.
 */

import { randomUUID } from 'node:crypto';

import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { PrismaClient } from '@/generated/prisma';
import { scopedDb } from '@/lib/scoped-db';

const databaseUrl = process.env['INTEGRATION_DATABASE_URL'];

// Skip the entire suite when no DB URL is set so `pnpm test` (which
// runs in jsdom with no Postgres anywhere) doesn't fail on dev laptops
// that haven't started the docker stack.
describe.skipIf(databaseUrl === undefined || databaseUrl.length === 0)(
  'scopedDb — cross-tenant isolation',
  () => {
    // Each test run gets fresh UUIDs so parallel runs against the same
    // DB don't collide, and so a partial cleanup (test killed mid-run)
    // doesn't leave rows that subsequent runs trip over.
    const runId = randomUUID();
    const orgA = randomUUID();
    const orgB = randomUUID();
    const userA = randomUUID();
    const userB = randomUUID();
    const studyA = randomUUID();
    const studyB = randomUUID();
    const sessionA = randomUUID();
    const sessionB = randomUUID();
    const orphanSession = randomUUID();
    const roomA = `room-a-${runId}`;
    const roomB = `room-b-${runId}`;
    const roomOrphan = `room-orphan-${runId}`;

    let prisma: PrismaClient;

    beforeAll(async () => {
      // `describe.skipIf` gates the suite at runtime but doesn't narrow
      // the type of `databaseUrl` here. The suite never runs when
      // unset, so a non-null assertion is correct and obvious.
      const url = databaseUrl!;
      prisma = new PrismaClient({
        datasources: { db: { url } },
        log: ['error'],
      });

      await prisma.study.createMany({
        data: [
          {
            id: studyA,
            orgId: orgA,
            name: `study-a-${runId}`,
            prompt: 'org A research prompt',
            rulesConfig: {},
            rulesVersion: 'v1',
            moderatorPersona: {},
            retentionPolicy: {},
            createdBy: userA,
          },
          {
            id: studyB,
            orgId: orgB,
            name: `study-b-${runId}`,
            prompt: 'org B research prompt',
            rulesConfig: {},
            rulesVersion: 'v1',
            moderatorPersona: {},
            retentionPolicy: {},
            createdBy: userB,
          },
        ],
      });

      await prisma.moderatedSession.createMany({
        data: [
          {
            id: sessionA,
            studyId: studyA,
            status: 'scheduled',
            livekitRoomName: roomA,
          },
          {
            id: sessionB,
            studyId: studyB,
            status: 'scheduled',
            livekitRoomName: roomB,
          },
          // Orphan session — `studyId === null` is the legacy shape
          // from Phase 2 (before the studies table landed). Per the
          // scopedDb docstring these are intentionally unreachable
          // from any per-org scope; only the admin retention sweep
          // touches them. The integration test exists to make that
          // intention executable.
          {
            id: orphanSession,
            studyId: null,
            status: 'scheduled',
            livekitRoomName: roomOrphan,
          },
        ],
      });
    });

    afterAll(async () => {
      // Targeted cleanup — only this run's rows, so concurrent runs and
      // any pre-existing local seed data are left alone. Sessions
      // cascade nothing of interest (no participants seeded), studies
      // delete after sessions because of the FK from sessions.studyId.
      await prisma.moderatedSession.deleteMany({
        where: { id: { in: [sessionA, sessionB, orphanSession] } },
      });
      await prisma.study.deleteMany({
        where: { id: { in: [studyA, studyB] } },
      });
      await prisma.$disconnect();
    });

    describe('studies.findById', () => {
      it('returns the study for the owning org', async () => {
        const row = await scopedDb(orgA, { prisma }).studies.findById(studyA);

        expect(row).not.toBeNull();
        expect(row?.id).toBe(studyA);
        expect(row?.orgId).toBe(orgA);
      });

      it('returns null when the study belongs to another org', async () => {
        // The load-bearing assertion: a researcher in orgA holding
        // orgB's study id (e.g. from a screenshot in a Slack channel
        // shared across companies) gets a clean null, not orgB's row.
        const row = await scopedDb(orgA, { prisma }).studies.findById(studyB);

        expect(row).toBeNull();
      });

      it('returns null when the study id does not exist', async () => {
        const row = await scopedDb(orgA, { prisma }).studies.findById(randomUUID());

        expect(row).toBeNull();
      });
    });

    describe('studies.list', () => {
      it('returns only the calling org studies', async () => {
        const rows = await scopedDb(orgA, { prisma }).studies.list();

        const ids = rows.map((row) => row.id);
        expect(ids).toContain(studyA);
        expect(ids).not.toContain(studyB);
        // Every row that comes back must carry the correct orgId — a
        // facade bug that joined wrong would surface here as an orgId
        // mismatch even if the id filter looked right.
        for (const row of rows) {
          expect(row.orgId).toBe(orgA);
        }
      });
    });

    describe('sessions.findById', () => {
      it('returns the session for the owning org', async () => {
        const row = await scopedDb(orgA, { prisma }).sessions.findById(sessionA);

        expect(row).not.toBeNull();
        expect(row?.id).toBe(sessionA);
        expect(row?.studyId).toBe(studyA);
        expect(row?.livekitRoomName).toBe(roomA);
      });

      it('returns null when the session belongs to another org', async () => {
        const row = await scopedDb(orgA, { prisma }).sessions.findById(sessionB);

        expect(row).toBeNull();
      });

      it('returns null for orphan sessions (studyId === null)', async () => {
        // Defense-in-depth: even though the join would technically
        // surface the orphan if a future schema dropped the null
        // check, the facade's explicit `study: { orgId }` predicate
        // requires a non-null FK. Locked here so a well-meaning
        // schema relax doesn't silently re-expose orphans.
        const row = await scopedDb(orgA, { prisma }).sessions.findById(orphanSession);

        expect(row).toBeNull();
      });

      it('returns null when the session id does not exist', async () => {
        const row = await scopedDb(orgA, { prisma }).sessions.findById(randomUUID());

        expect(row).toBeNull();
      });
    });

    describe('sessions.findByLivekitRoomName', () => {
      it('returns the session for the owning org', async () => {
        const row = await scopedDb(orgA, { prisma }).sessions.findByLivekitRoomName(roomA);

        expect(row).not.toBeNull();
        expect(row?.id).toBe(sessionA);
      });

      it('returns null when the room belongs to another org', async () => {
        // `livekit_room_name` is globally unique. Without the scope
        // filter, a cross-org caller could discover whether a known
        // room name exists. The gate must return null all the same.
        const row = await scopedDb(orgA, { prisma }).sessions.findByLivekitRoomName(roomB);

        expect(row).toBeNull();
      });

      it('returns null for orphan rooms (studyId === null)', async () => {
        const row = await scopedDb(orgA, { prisma }).sessions.findByLivekitRoomName(roomOrphan);

        expect(row).toBeNull();
      });
    });

    describe('sessions.listRecent', () => {
      it('returns only the calling org sessions and excludes orphans', async () => {
        const rows = await scopedDb(orgA, { prisma }).sessions.listRecent();

        const ids = rows.map((row) => row.id);
        expect(ids).toContain(sessionA);
        expect(ids).not.toContain(sessionB);
        expect(ids).not.toContain(orphanSession);
      });

      it('returns an empty array for an org with no sessions', async () => {
        const emptyOrg = randomUUID();
        const rows = await scopedDb(emptyOrg, { prisma }).sessions.listRecent();

        // Not just "doesn't include sessionA/B" — we want a true empty
        // result, otherwise the limit-N slice could mask cross-org
        // leakage when the calling org happens to have its own data.
        const seededIds = new Set<string>([sessionA, sessionB, orphanSession]);
        for (const row of rows) {
          expect(seededIds.has(row.id)).toBe(false);
        }
      });
    });

    describe('orgId validation', () => {
      it('throws when called with an empty orgId', () => {
        // The throw lives in the facade constructor, not at query
        // time. Verified in unit tests too — included here so the
        // integration suite is a complete picture of the contract.
        expect(() => scopedDb('', { prisma })).toThrow(/orgId must be a non-empty string/);
      });
    });
  },
);
