/**
 * Concrete `RetentionRepo` + `R2KeyDeleter` wired against Prisma + R2.
 *
 * The runner stays repo-agnostic — this module is the only place that
 * imports Prisma in the retention feature. Tests use the in-memory
 * fake in `runner.test.ts`; production wires the real Prisma client
 * here via `createPrismaRetentionRepo()`.
 *
 * Streaming order: studies are walked in `(orgId, createdAt)` order
 * — deterministic across runs and roughly matches the order
 * researchers created them, so logs read naturally. Within each
 * study, sessions stream ENDED-only and oldest-first, so the runner
 * does the most-work-per-row sessions first (long-finished sessions
 * with the largest snapshot footprints).
 *
 * The "no-study" group at the end picks up sessions whose `studyId`
 * is null — those exist because Phase 2 created sessions before the
 * studies table landed, and tests still create study-less sessions.
 * They use `DEFAULT_RETENTION_POLICY`.
 */

import 'server-only';

import { deleteR2Keys } from '@/features/recordings/r2';
import { parseParticipantRecordingUrls } from '@/features/recordings/replay-urls';
import { Prisma } from '@/generated/prisma';
import type { PrismaClient } from '@/generated/prisma';
import { db } from '@/lib/db';

import { DEFAULT_RETENTION_POLICY, parseRetentionPolicy } from './policy';

import type {
  R2KeyDeleter,
  RetentionRepo,
  RetentionSessionView,
  RetentionStudyView,
  TranscriptDeleteCounts,
} from './runner';
import type { SnapshotDownsampleMeta } from './snapshot-downsample';

const STUDY_PAGE_SIZE = 50;
const SESSION_PAGE_SIZE = 100;

/**
 * Build the production `RetentionRepo`. The `prisma` arg is the
 * default `db` singleton; tests can pass an alternate client (e.g. a
 * Prisma instance pointed at a test database).
 */
export function createPrismaRetentionRepo(prisma: PrismaClient = db): RetentionRepo {
  return {
    iterateStudies(): AsyncIterable<RetentionStudyView> {
      return iterateAllStudies(prisma);
    },
    async listSnapshotMeta(sessionId: string): Promise<SnapshotDownsampleMeta[]> {
      const rows = await prisma.stateSnapshot.findMany({
        where: { sessionId },
        orderBy: [{ tickId: 'asc' }, { id: 'asc' }],
        select: { id: true, ts: true, tickId: true },
      });
      return rows.map((row) => ({ id: row.id, ts: row.ts, tickId: row.tickId }));
    },
    async deleteSnapshotsByIds(ids: readonly string[]): Promise<number> {
      if (ids.length === 0) return 0;
      const { count } = await prisma.stateSnapshot.deleteMany({
        where: { id: { in: [...ids] } },
      });
      return count;
    },
    async deleteAllSnapshotsForSession(sessionId: string): Promise<number> {
      const { count } = await prisma.stateSnapshot.deleteMany({ where: { sessionId } });
      return count;
    },
    async deleteTranscriptsForSession(sessionId: string): Promise<TranscriptDeleteCounts> {
      // `rule_evaluations` cascade from `decisions` (onDelete: Cascade
      // in the migration), so we don't issue a separate DELETE for them.
      // Order matters only for foreign-key arrows; everything below
      // either cascades from sessions or is a leaf row in this scope.
      return prisma.$transaction(async (tx) => {
        const utterances = await tx.utterance.deleteMany({ where: { sessionId } });
        const decisions = await tx.decision.deleteMany({ where: { sessionId } });
        const flags = await tx.sessionFlag.deleteMany({ where: { sessionId } });
        const actions = await tx.researcherAction.deleteMany({ where: { sessionId } });
        return {
          utterances: utterances.count,
          decisions: decisions.count,
          flags: flags.count,
          actions: actions.count,
        };
      });
    },
    async clearRecordingPointers(sessionId: string): Promise<void> {
      await prisma.moderatedSession.update({
        where: { id: sessionId },
        data: {
          recordingUrl: null,
          // JSONB columns need the `Prisma.DbNull` sentinel for "set the
          // column to SQL NULL" — a bare `null` would be rejected by
          // the generated client.
          perParticipantRecordingUrls: Prisma.DbNull,
        },
      });
    },
  };
}

/**
 * Default `R2KeyDeleter` wired through the shared R2 helper.
 *
 * Tests inject a stub; production points the runner at this.
 */
export const defaultR2KeyDeleter: R2KeyDeleter = {
  async deleteKeys(keys: readonly string[]): Promise<{ deleted: number; errors: string[] }> {
    return deleteR2Keys(keys);
  },
};

async function* iterateAllStudies(
  prisma: PrismaClient,
): AsyncGenerator<RetentionStudyView, void, void> {
  // Studies — paginate by `(orgId, createdAt, id)` so a study added
  // mid-sweep doesn't get skipped or re-processed.
  let cursorCreatedAt: Date | undefined;
  let cursorId: string | undefined;
  let hasMore = true;
  while (hasMore) {
    // `exactOptionalPropertyTypes` rejects `where: undefined`, so we
    // build the args object conditionally — the first page omits the
    // `where` key entirely, subsequent pages add it.
    const studies = await prisma.study.findMany({
      ...(cursorCreatedAt !== undefined && cursorId !== undefined
        ? {
            where: {
              OR: [
                { createdAt: { gt: cursorCreatedAt } },
                { createdAt: cursorCreatedAt, id: { gt: cursorId } },
              ],
            },
          }
        : {}),
      orderBy: [{ createdAt: 'asc' }, { id: 'asc' }],
      take: STUDY_PAGE_SIZE,
      select: { id: true, createdAt: true, retentionPolicy: true },
    });
    if (studies.length === 0) break;
    for (const study of studies) {
      const policy = parseRetentionPolicy(study.retentionPolicy);
      yield {
        studyId: study.id,
        policy,
        sessions: iterateSessionsForStudy(prisma, study.id),
      };
    }
    const last = studies[studies.length - 1];
    if (last === undefined) break;
    cursorCreatedAt = last.createdAt;
    cursorId = last.id;
    hasMore = studies.length === STUDY_PAGE_SIZE;
  }

  // Trailing "no study" group — sessions without a `study_id`.
  yield {
    studyId: null,
    policy: DEFAULT_RETENTION_POLICY,
    sessions: iterateSessionsForStudy(prisma, null),
  };
}

async function* iterateSessionsForStudy(
  prisma: PrismaClient,
  studyId: string | null,
): AsyncGenerator<RetentionSessionView, void, void> {
  // Cursor on `(actualEnd, id)` ASC. Live sessions are excluded — the
  // planner skips them anyway, but excluding here avoids a SELECT of
  // all in-flight sessions per study.
  let cursorActualEnd: Date | undefined;
  let cursorId: string | undefined;
  let hasMore = true;
  const baseWhere = {
    studyId: studyId,
    actualEnd: { not: null },
  } as const;
  while (hasMore) {
    const sessions = await prisma.moderatedSession.findMany({
      where: {
        ...baseWhere,
        ...(cursorActualEnd !== undefined && cursorId !== undefined
          ? {
              OR: [
                { actualEnd: { gt: cursorActualEnd } },
                { actualEnd: cursorActualEnd, id: { gt: cursorId } },
              ],
            }
          : {}),
      },
      orderBy: [{ actualEnd: 'asc' }, { id: 'asc' }],
      take: SESSION_PAGE_SIZE,
      select: {
        id: true,
        actualEnd: true,
        recordingUrl: true,
        perParticipantRecordingUrls: true,
      },
    });
    if (sessions.length === 0) break;
    for (const session of sessions) {
      yield {
        id: session.id,
        actualEnd: session.actualEnd,
        recordingUrl: session.recordingUrl,
        perParticipantRecordingUrls: parseParticipantRecordingUrls(
          session.perParticipantRecordingUrls,
        ),
      };
    }
    const last = sessions[sessions.length - 1];
    if (last?.actualEnd === undefined || last.actualEnd === null) break;
    cursorActualEnd = last.actualEnd;
    cursorId = last.id;
    hasMore = sessions.length === SESSION_PAGE_SIZE;
  }
}
