/**
 * Retention feature — public surface.
 *
 * The route handler (`/api/admin/retention/run`) imports `runRetention`
 * + the Prisma-backed repo + the R2 deleter from here. Per-study
 * `RetentionPolicy` shape + parser are also exported so the study
 * editor UI (Phase 7 hardening) can validate user input against the
 * same schema the runner consumes.
 */

export { DEFAULT_RETENTION_POLICY, parseRetentionPolicy, RetentionPolicySchema } from './policy';
export type { RetentionPolicy } from './policy';

export { planRetentionActions } from './planner';
export type { RetentionActions, RetentionPlanInput } from './planner';

export { selectSnapshotIdsToDropForDownsample } from './snapshot-downsample';
export type { SnapshotDownsampleMeta } from './snapshot-downsample';

export { runRetention } from './runner';
export type {
  PerSessionError,
  R2KeyDeleter,
  RetentionLogger,
  RetentionRepo,
  RetentionRunOptions,
  RetentionRunResult,
  RetentionSessionView,
  RetentionStudyView,
  TranscriptDeleteCounts,
} from './runner';

export { createPrismaRetentionRepo, defaultR2KeyDeleter } from './prisma-repo';
