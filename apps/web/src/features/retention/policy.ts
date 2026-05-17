/**
 * Per-study retention policy — schema + defaults.
 *
 * A study row carries an opaque `retention_policy` JSONB column (brief
 * §10.1, §3 IRB posture). This module is the canonical shape the
 * retention runner reads it back as, and the defaults applied when a
 * study leaves it empty (the common case for early studies).
 *
 * Knobs (all ages are days, measured from `sessions.actual_end`):
 *   - `recordingsTTLDays`           — delete the R2 mp4 (composite +
 *                                     per-participant tracks) after N
 *                                     days. `null` → keep forever.
 *   - `snapshotsDownsampleAfterDays`— drop state_snapshots down to 1 Hz
 *                                     (one row per second-bucket) after
 *                                     N days. The dense 2 Hz stream is
 *                                     trivial to store fresh and
 *                                     irreplaceable for fine-grained
 *                                     replay; after the replay window
 *                                     closes, 1 Hz is enough for
 *                                     trend-level audit (brief §10.2).
 *   - `snapshotsTTLDays`            — delete ALL state_snapshots after
 *                                     N days. `null` → keep forever
 *                                     (post-downsampling). Set this
 *                                     when the storage cost matters
 *                                     more than long-tail audit.
 *   - `transcriptsTTLDays`          — purge utterances + decisions +
 *                                     rule_evaluations + researcher_actions
 *                                     + session_flags after N days.
 *                                     `null` → keep forever (the
 *                                     IRB-friendly default — the audit
 *                                     trail is part of the product per
 *                                     brief §2.2 and the safer choice
 *                                     when in doubt).
 *
 * Why `null = forever` rather than a sentinel like `0`: explicit
 * three-valued logic (`number | null`) reads cleaner at the call site
 * (`policy.recordingsTTLDays === null` is unambiguous) and rules out
 * the off-by-one bug class where `0 days` means "delete on first
 * sweep" instead of "keep forever".
 *
 * Why a Zod schema rather than a plain TS interface: the JSONB column
 * may carry an old shape (a forward-compat addition, a legacy field
 * since removed). `parseRetentionPolicy` rejects obvious corruption
 * and falls back to defaults rather than crashing the daily sweep —
 * one bad study config must not stall every other study's retention.
 */

import { z } from 'zod';

const positiveIntOrNull = z
  .union([z.number().int().positive(), z.null()])
  .describe('Days; null disables this rule');

export const RetentionPolicySchema = z
  .object({
    recordingsTTLDays: positiveIntOrNull.default(365),
    snapshotsDownsampleAfterDays: z.number().int().positive().default(30),
    snapshotsTTLDays: positiveIntOrNull.default(null),
    transcriptsTTLDays: positiveIntOrNull.default(null),
  })
  // `passthrough()` (not `strict()`) so a study's JSONB can carry
  // forward-compat fields the runner doesn't read yet without failing
  // the sweep. Unknown fields are silently dropped after parsing.
  .passthrough();

export type RetentionPolicy = z.infer<typeof RetentionPolicySchema>;

/**
 * Defaults used when a study has an empty `retention_policy`, when no
 * study is attached to a session at all, or when the column fails to
 * parse. The defaults are deliberately conservative: keep recordings
 * for a year (long enough for a typical research engagement), keep
 * transcripts forever (audit), downsample snapshots at 30 days (the
 * brief's stated threshold).
 */
export const DEFAULT_RETENTION_POLICY: RetentionPolicy = RetentionPolicySchema.parse({});

/**
 * Coerce a raw JSONB blob into a `RetentionPolicy`.
 *
 * Returns the parsed policy on success. On *any* failure (wrong type,
 * negative days, malformed shape), returns `DEFAULT_RETENTION_POLICY`
 * — the runner gets a usable policy in every case, and the bad row is
 * surfaced via the optional `onWarn` hook so ops can fix it without
 * the sweep grinding to a halt.
 */
export function parseRetentionPolicy(
  raw: unknown,
  onWarn?: (message: string) => void,
): RetentionPolicy {
  // `null` or empty object → defaults silently. Anything else that
  // fails validation gets warned about.
  if (raw === null || raw === undefined) return DEFAULT_RETENTION_POLICY;
  const result = RetentionPolicySchema.safeParse(raw);
  if (result.success) return result.data;
  if (onWarn !== undefined) {
    onWarn(
      `retention_policy parse failed; falling back to defaults: ${result.error.issues
        .map((i) => `${i.path.join('.') || '(root)'}: ${i.message}`)
        .join('; ')}`,
    );
  }
  return DEFAULT_RETENTION_POLICY;
}
