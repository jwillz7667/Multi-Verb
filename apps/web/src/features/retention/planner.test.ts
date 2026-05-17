/**
 * Tests for `planRetentionActions` — the pure decision function the
 * runner consults per session.
 *
 * Pins:
 *   - live sessions (`actualEnd === null`) are no-ops,
 *   - sessions younger than the downsample threshold are untouched,
 *   - sessions past downsample but before TTL get downsampled only,
 *   - sessions past snapshot TTL get delete-all (downsample suppressed),
 *   - `null` TTLs disable that particular action,
 *   - `deleteRecordings` is skipped when there's no recording to delete,
 *   - clock skew (future `actualEnd`) is tolerated — no actions.
 */

import { describe, expect, it } from 'vitest';

import { planRetentionActions } from './planner';
import { DEFAULT_RETENTION_POLICY, type RetentionPolicy } from './policy';

const NOW = new Date('2026-05-17T10:00:00.000Z');

function daysAgo(n: number): Date {
  return new Date(NOW.getTime() - n * 24 * 60 * 60 * 1000);
}

function policy(overrides: Partial<RetentionPolicy> = {}): RetentionPolicy {
  return { ...DEFAULT_RETENTION_POLICY, ...overrides };
}

describe('planRetentionActions', () => {
  it('returns all-false for a live session (actualEnd === null)', () => {
    expect(planRetentionActions({ actualEnd: null, hasRecording: true }, policy(), NOW)).toEqual({
      downsampleSnapshots: false,
      deleteAllSnapshots: false,
      deleteRecordings: false,
      deleteTranscripts: false,
    });
  });

  it('returns all-false for a future-dated actualEnd (clock skew tolerance)', () => {
    // Skewed clock or a test fixture set `actual_end` 1h ahead of NOW.
    // We must not eagerly delete on the strength of that.
    const futureEnd = new Date(NOW.getTime() + 60 * 60 * 1000);
    expect(
      planRetentionActions({ actualEnd: futureEnd, hasRecording: true }, policy(), NOW),
    ).toEqual({
      downsampleSnapshots: false,
      deleteAllSnapshots: false,
      deleteRecordings: false,
      deleteTranscripts: false,
    });
  });

  it('returns all-false for a freshly-ended session (under all thresholds)', () => {
    // Defaults: downsample 30d, recordings 365d, snapshots/transcripts forever.
    // A 1-day-old session sits under every limit.
    expect(
      planRetentionActions({ actualEnd: daysAgo(1), hasRecording: true }, policy(), NOW),
    ).toEqual({
      downsampleSnapshots: false,
      deleteAllSnapshots: false,
      deleteRecordings: false,
      deleteTranscripts: false,
    });
  });

  it('downsamples (only) when past the snapshot-downsample window but under snapshot TTL', () => {
    expect(
      planRetentionActions({ actualEnd: daysAgo(40), hasRecording: true }, policy(), NOW),
    ).toEqual({
      downsampleSnapshots: true,
      deleteAllSnapshots: false,
      deleteRecordings: false,
      deleteTranscripts: false,
    });
  });

  it('deletes recordings when past recordings TTL', () => {
    // 400 days > default 365 — recordings expire; snapshots still in
    // downsample-only territory (no snapshot TTL set by default).
    expect(
      planRetentionActions({ actualEnd: daysAgo(400), hasRecording: true }, policy(), NOW),
    ).toEqual({
      downsampleSnapshots: true,
      deleteAllSnapshots: false,
      deleteRecordings: true,
      deleteTranscripts: false,
    });
  });

  it('skips deleteRecordings when there is no recording to delete', () => {
    const out = planRetentionActions(
      { actualEnd: daysAgo(400), hasRecording: false },
      policy(),
      NOW,
    );
    expect(out.deleteRecordings).toBe(false);
  });

  it('chooses delete-all over downsample when snapshots TTL is also reached', () => {
    // Custom policy: downsample at 30d, delete all snapshots at 90d.
    // A 100-day-old session is past both — we should NOT downsample
    // (no point) and instead go straight to delete-all.
    const p = policy({ snapshotsDownsampleAfterDays: 30, snapshotsTTLDays: 90 });
    expect(planRetentionActions({ actualEnd: daysAgo(100), hasRecording: true }, p, NOW)).toEqual({
      downsampleSnapshots: false,
      deleteAllSnapshots: true,
      deleteRecordings: false,
      deleteTranscripts: false,
    });
  });

  it('respects null TTLs as "never delete" sentinels', () => {
    // Explicit nulls — recordings, snapshot TTL, transcripts all
    // disabled. Even at 10,000 days only downsample fires.
    const p = policy({
      recordingsTTLDays: null,
      snapshotsTTLDays: null,
      transcriptsTTLDays: null,
    });
    expect(
      planRetentionActions({ actualEnd: daysAgo(10_000), hasRecording: true }, p, NOW),
    ).toEqual({
      downsampleSnapshots: true,
      deleteAllSnapshots: false,
      deleteRecordings: false,
      deleteTranscripts: false,
    });
  });

  it('deletes transcripts when transcripts TTL is set and reached', () => {
    const p = policy({ transcriptsTTLDays: 365 });
    expect(planRetentionActions({ actualEnd: daysAgo(400), hasRecording: false }, p, NOW)).toEqual({
      downsampleSnapshots: true,
      deleteAllSnapshots: false,
      deleteRecordings: false,
      deleteTranscripts: true,
    });
  });
});
