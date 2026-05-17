/**
 * Pure planner — given a session's lifecycle state, the study's
 * retention policy, and "now", produce the set of retention actions
 * the runner should execute on that session.
 *
 * Splitting the planner out keeps the policy logic unit-testable
 * without any Prisma / R2 doubles. The runner is then a thin
 * orchestrator: ask the planner what to do, ask the repo to do it,
 * tally the result, move on.
 *
 * Composition rule (small but important): if a session is past its
 * `snapshotsTTLDays` we DELETE all its snapshots — there's no point
 * spending the SELECT-then-chunked-DELETE cycle of downsampling first
 * just to drop them on the next pass. `deleteAllSnapshots` therefore
 * implies `!downsampleSnapshots`.
 *
 * Sessions still running (`actualEnd === null`) get an all-`false`
 * action set. Retention measures from `actual_end`, and a live session
 * has none. The runner skips them quickly without ever touching the
 * snapshot table.
 */

import type { RetentionPolicy } from './policy';

export interface RetentionPlanInput {
  /** Session `actual_end` — null if the session hasn't ended yet. */
  actualEnd: Date | null;
  /**
   * Whether the session has a composite recording landed in R2.
   * Sourced from `sessions.recording_url !== null`. We skip the
   * R2-delete branch entirely when this is false, even if the policy
   * would otherwise fire — saving one no-op DELETE per session.
   */
  hasRecording: boolean;
}

export interface RetentionActions {
  /**
   * Downsample state_snapshots to 1 Hz (one row per second-bucket).
   * Mutually exclusive with `deleteAllSnapshots`; if both ages are
   * reached we skip downsampling and go straight to delete-all.
   */
  downsampleSnapshots: boolean;
  /** DELETE every state_snapshots row for this session. */
  deleteAllSnapshots: boolean;
  /**
   * DELETE the R2 composite + per-participant tracks and null out the
   * `recording_url` / `per_participant_recording_urls` pointers.
   */
  deleteRecordings: boolean;
  /**
   * Cascade-DELETE the transcript / decision audit trail
   * (utterances + decisions + rule_evaluations + researcher_actions +
   * session_flags). Default policy keeps this forever.
   */
  deleteTranscripts: boolean;
}

const NO_OP_ACTIONS: RetentionActions = Object.freeze({
  downsampleSnapshots: false,
  deleteAllSnapshots: false,
  deleteRecordings: false,
  deleteTranscripts: false,
});

const MS_PER_DAY = 24 * 60 * 60 * 1000;

/**
 * Compute the retention actions for one session against one policy.
 *
 * Always returns an immutable shape — the runner can hand it straight
 * to log lines without worrying about subsequent mutation.
 */
export function planRetentionActions(
  input: RetentionPlanInput,
  policy: RetentionPolicy,
  now: Date,
): RetentionActions {
  if (input.actualEnd === null) {
    return NO_OP_ACTIONS;
  }
  // A future-dated `actualEnd` (clock skew, test fixture) reads as a
  // negative age — treat the session as "too young" rather than
  // crash. The runner will pick it up on the next pass when the wall
  // clock catches up.
  const ageDays = (now.getTime() - input.actualEnd.getTime()) / MS_PER_DAY;
  if (ageDays < 0) return NO_OP_ACTIONS;

  const ageReached = (limitDays: number | null): boolean =>
    limitDays !== null && ageDays >= limitDays;

  const deleteAllSnapshots = ageReached(policy.snapshotsTTLDays);
  const downsampleAgeReached = ageDays >= policy.snapshotsDownsampleAfterDays;

  return {
    // Skip downsampling if we're about to nuke everything anyway.
    downsampleSnapshots: downsampleAgeReached && !deleteAllSnapshots,
    deleteAllSnapshots,
    deleteRecordings: ageReached(policy.recordingsTTLDays) && input.hasRecording,
    deleteTranscripts: ageReached(policy.transcriptsTTLDays),
  };
}
