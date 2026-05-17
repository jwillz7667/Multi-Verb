/**
 * Retention runner — orchestrates per-study + per-session retention.
 *
 * Pure planner (`planner.ts`) + pure downsample helper
 * (`snapshot-downsample.ts`) + thin async iterators on the repo. The
 * runner itself owns:
 *   - iteration order (studies first, then sessions within each study,
 *     so a study with hundreds of sessions never starves the rest),
 *   - chunked deletes (state_snapshots are deleted in batches of 500
 *     ids to keep the parameter list bounded),
 *   - per-session error isolation (one bad session never stops the
 *     sweep — it's recorded and we move on),
 *   - a single `RetentionRunResult` accumulator so the route can hand
 *     ops a one-line summary of what just happened.
 *
 * Why per-session try/catch rather than letting one failure abort:
 * the daily sweep runs unattended, and the consequence of stopping
 * early is "the rest of the table accumulates stale data until someone
 * notices". The consequence of continuing past one row's failure is
 * "the operator sees the error in the summary and re-runs that one
 * session". The asymmetry is the whole reason this is a job, not a
 * transaction.
 */

import 'server-only';

import { planRetentionActions, type RetentionActions } from './planner';
import { DEFAULT_RETENTION_POLICY, type RetentionPolicy } from './policy';
import {
  selectSnapshotIdsToDropForDownsample,
  type SnapshotDownsampleMeta,
} from './snapshot-downsample';

export interface RetentionSessionView {
  id: string;
  actualEnd: Date | null;
  /**
   * R2 key for the composite recording (e.g. `sessions/{uuid}/composite.mp4`),
   * or `null` if the egress hasn't landed yet. The runner uses this
   * both to decide whether to issue an R2 DELETE and as the key list
   * to delete.
   */
  recordingUrl: string | null;
  /**
   * Identity → R2-key map for per-participant tracks. Empty when the
   * per-participant egress hasn't run or hasn't completed.
   */
  perParticipantRecordingUrls: Record<string, string>;
}

export interface RetentionStudyView {
  /** `null` for sessions without a study attached — they use the default policy. */
  studyId: string | null;
  policy: RetentionPolicy;
  sessions: AsyncIterable<RetentionSessionView>;
}

export interface TranscriptDeleteCounts {
  utterances: number;
  decisions: number;
  flags: number;
  actions: number;
}

/**
 * Data-layer facade the runner consumes. Tests inject an in-memory
 * implementation; production wires it up against Prisma + R2 via
 * `prisma-repo.ts`.
 */
export interface RetentionRepo {
  /**
   * Yield one entry per study (plus one trailing "null-study" group
   * for sessions that don't reference a study row). Sessions within a
   * group are streamed lazily so we never hold the full sessions
   * table in memory.
   */
  iterateStudies(): AsyncIterable<RetentionStudyView>;
  /**
   * Cheap meta projection for downsampling (id + ts + tick id only,
   * never the full `state` JSONB).
   */
  listSnapshotMeta(sessionId: string): Promise<SnapshotDownsampleMeta[]>;
  /**
   * DELETE state_snapshots by id. Caller is expected to chunk to a
   * sane size; the runner does so at 500 per call.
   */
  deleteSnapshotsByIds(ids: readonly string[]): Promise<number>;
  /** DELETE every state_snapshots row for one session. */
  deleteAllSnapshotsForSession(sessionId: string): Promise<number>;
  /**
   * Cascade-DELETE the transcript / audit trail rows for one session
   * (utterances + decisions + rule_evaluations + researcher_actions +
   * session_flags). Returns per-table counts for the summary.
   */
  deleteTranscriptsForSession(sessionId: string): Promise<TranscriptDeleteCounts>;
  /**
   * Null out `sessions.recording_url` and `per_participant_recording_urls`
   * after the R2 objects have been deleted. Always called AFTER the
   * R2 delete so we never leave a dangling pointer to a missing
   * object.
   */
  clearRecordingPointers(sessionId: string): Promise<void>;
}

/**
 * R2 side of the runner. Same shape as `RetentionRepo` so the runner
 * stays repo-agnostic — production wires it up against the S3 client
 * in `features/recordings/r2.ts`.
 */
export interface R2KeyDeleter {
  /**
   * Delete a batch of keys from the bucket. Should return without
   * throwing on partial failures (the runner can't recover anyway);
   * report them via the optional `onError` hook on the options.
   */
  deleteKeys(keys: readonly string[]): Promise<{ deleted: number; errors: string[] }>;
}

export interface RetentionRunOptions {
  /**
   * Override the wall clock for tests. Defaults to `new Date()`.
   */
  now?: Date;
  /**
   * Cap the number of snapshot ids per DELETE statement. Bounds the
   * parameter-list size for Prisma + Postgres; 500 is comfortably
   * under the typical 32k bind limit and keeps each query short
   * enough to read in a log line.
   */
  snapshotDeleteChunkSize?: number;
  /**
   * Structured log sink. Tests pass a vi.fn; production passes the
   * real logger from `lib/logger` or just `console`.
   */
  logger?: RetentionLogger;
}

export interface RetentionLogger {
  info(message: string, fields?: Record<string, unknown>): void;
  warn(message: string, fields?: Record<string, unknown>): void;
  error(message: string, fields?: Record<string, unknown>): void;
}

export interface PerSessionError {
  sessionId: string;
  studyId: string | null;
  message: string;
}

export interface RetentionRunResult {
  studiesProcessed: number;
  sessionsProcessed: number;
  sessionsSkipped: number; // live or under all thresholds
  snapshotsDeleted: number;
  recordingsDeleted: number;
  transcriptsDeleted: number;
  errors: PerSessionError[];
}

const DEFAULT_SNAPSHOT_CHUNK = 500;

function emptyResult(): RetentionRunResult {
  return {
    studiesProcessed: 0,
    sessionsProcessed: 0,
    sessionsSkipped: 0,
    snapshotsDeleted: 0,
    recordingsDeleted: 0,
    transcriptsDeleted: 0,
    errors: [],
  };
}

function actionsAreNoOp(a: RetentionActions): boolean {
  return (
    !a.downsampleSnapshots && !a.deleteAllSnapshots && !a.deleteRecordings && !a.deleteTranscripts
  );
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === 'string') return err;
  return JSON.stringify(err);
}

/**
 * Run the retention sweep across every study (and the null-study
 * bucket). Returns a summary; errors are accumulated rather than
 * thrown so the daily cron can log a result row even if some sessions
 * failed.
 */
export async function runRetention(
  repo: RetentionRepo,
  r2: R2KeyDeleter,
  options: RetentionRunOptions = {},
): Promise<RetentionRunResult> {
  const now = options.now ?? new Date();
  const chunkSize = options.snapshotDeleteChunkSize ?? DEFAULT_SNAPSHOT_CHUNK;
  const logger = options.logger;
  const result = emptyResult();

  for await (const study of repo.iterateStudies()) {
    result.studiesProcessed += 1;
    const studyTag = study.studyId ?? '(no-study)';
    logger?.info('retention.study.start', { studyId: studyTag });

    for await (const session of study.sessions) {
      try {
        await processOneSession({
          session,
          policy: study.policy,
          studyId: study.studyId,
          now,
          chunkSize,
          repo,
          r2,
          result,
          logger,
        });
      } catch (err) {
        const message = errorMessage(err);
        result.errors.push({
          sessionId: session.id,
          studyId: study.studyId,
          message,
        });
        logger?.error('retention.session.error', {
          sessionId: session.id,
          studyId: studyTag,
          error: message,
        });
      }
    }
  }

  logger?.info('retention.run.complete', {
    studies: result.studiesProcessed,
    sessions: result.sessionsProcessed,
    skipped: result.sessionsSkipped,
    snapshotsDeleted: result.snapshotsDeleted,
    recordingsDeleted: result.recordingsDeleted,
    transcriptsDeleted: result.transcriptsDeleted,
    errors: result.errors.length,
  });

  return result;
}

interface ProcessArgs {
  session: RetentionSessionView;
  policy: RetentionPolicy;
  studyId: string | null;
  now: Date;
  chunkSize: number;
  repo: RetentionRepo;
  r2: R2KeyDeleter;
  result: RetentionRunResult;
  logger: RetentionLogger | undefined;
}

async function processOneSession(args: ProcessArgs): Promise<void> {
  const { session, policy, now, chunkSize, repo, r2, result, logger } = args;

  const actions = planRetentionActions(
    { actualEnd: session.actualEnd, hasRecording: session.recordingUrl !== null },
    policy,
    now,
  );

  if (actionsAreNoOp(actions)) {
    result.sessionsSkipped += 1;
    return;
  }
  result.sessionsProcessed += 1;

  if (actions.deleteAllSnapshots) {
    const n = await repo.deleteAllSnapshotsForSession(session.id);
    result.snapshotsDeleted += n;
    logger?.info('retention.snapshots.delete_all', { sessionId: session.id, count: n });
  } else if (actions.downsampleSnapshots) {
    const meta = await repo.listSnapshotMeta(session.id);
    const ids = selectSnapshotIdsToDropForDownsample(meta);
    if (ids.length > 0) {
      for (let i = 0; i < ids.length; i += chunkSize) {
        const chunk = ids.slice(i, i + chunkSize);
        const n = await repo.deleteSnapshotsByIds(chunk);
        result.snapshotsDeleted += n;
      }
      logger?.info('retention.snapshots.downsample', {
        sessionId: session.id,
        droppedRows: ids.length,
      });
    }
  }

  if (actions.deleteRecordings) {
    const keys = collectRecordingKeys(session);
    if (keys.length > 0) {
      const r2Result = await r2.deleteKeys(keys);
      result.recordingsDeleted += r2Result.deleted;
      if (r2Result.errors.length > 0) {
        // Partial-failure case: record but don't throw — the pointer
        // cleanup below would only run if the R2 delete succeeded,
        // and we'd rather null pointers to gone objects than leave
        // both rows AND objects orphaned.
        logger?.warn('retention.recordings.partial_error', {
          sessionId: session.id,
          errors: r2Result.errors,
        });
      }
    }
    await repo.clearRecordingPointers(session.id);
    logger?.info('retention.recordings.deleted', {
      sessionId: session.id,
      keys: keys.length,
    });
  }

  if (actions.deleteTranscripts) {
    const counts = await repo.deleteTranscriptsForSession(session.id);
    const total = counts.utterances + counts.decisions + counts.flags + counts.actions;
    result.transcriptsDeleted += total;
    logger?.info('retention.transcripts.deleted', { sessionId: session.id, ...counts });
  }
}

function collectRecordingKeys(session: RetentionSessionView): string[] {
  const keys: string[] = [];
  if (session.recordingUrl !== null && session.recordingUrl.length > 0) {
    keys.push(session.recordingUrl);
  }
  for (const key of Object.values(session.perParticipantRecordingUrls)) {
    if (key.length > 0) keys.push(key);
  }
  return keys;
}

// Re-export the default policy here so the route's repo wiring can
// pick it up from a single barrel import.
export { DEFAULT_RETENTION_POLICY };
