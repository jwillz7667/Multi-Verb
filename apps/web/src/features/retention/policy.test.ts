/**
 * Tests for the per-study retention policy schema.
 *
 * Pins:
 *   - the defaults match the brief: snapshots downsample at 30 days,
 *     recordings expire after a year, transcripts kept forever (IRB).
 *   - explicit `null` overrides default — a study may opt into "keep
 *     recordings forever" without losing the other knobs.
 *   - `parseRetentionPolicy` never throws; bad shapes fall back to
 *     defaults so one corrupt JSONB row can't stall the daily sweep.
 *   - unknown forward-compat fields pass through without failure.
 */

import { describe, expect, it, vi } from 'vitest';

import { DEFAULT_RETENTION_POLICY, parseRetentionPolicy, RetentionPolicySchema } from './policy';

describe('DEFAULT_RETENTION_POLICY', () => {
  it('matches the brief defaults — downsample at 30d, recordings 1yr, transcripts forever', () => {
    expect(DEFAULT_RETENTION_POLICY).toEqual({
      recordingsTTLDays: 365,
      snapshotsDownsampleAfterDays: 30,
      snapshotsTTLDays: null,
      transcriptsTTLDays: null,
    });
  });
});

describe('RetentionPolicySchema', () => {
  it('accepts a fully-specified policy', () => {
    const parsed = RetentionPolicySchema.parse({
      recordingsTTLDays: 90,
      snapshotsDownsampleAfterDays: 7,
      snapshotsTTLDays: 180,
      transcriptsTTLDays: 365,
    });
    expect(parsed.recordingsTTLDays).toBe(90);
    expect(parsed.snapshotsDownsampleAfterDays).toBe(7);
    expect(parsed.snapshotsTTLDays).toBe(180);
    expect(parsed.transcriptsTTLDays).toBe(365);
  });

  it('honors explicit nulls — opting OUT of a TTL is not the same as the default', () => {
    const parsed = RetentionPolicySchema.parse({
      recordingsTTLDays: null,
      snapshotsTTLDays: null,
      transcriptsTTLDays: null,
    });
    expect(parsed.recordingsTTLDays).toBeNull();
    expect(parsed.snapshotsTTLDays).toBeNull();
    expect(parsed.transcriptsTTLDays).toBeNull();
    // Downsample window still defaulted.
    expect(parsed.snapshotsDownsampleAfterDays).toBe(30);
  });

  it('rejects zero / negative days — a "0 days TTL" knob is too easy to set by accident', () => {
    expect(() => RetentionPolicySchema.parse({ recordingsTTLDays: 0 })).toThrow();
    expect(() => RetentionPolicySchema.parse({ recordingsTTLDays: -1 })).toThrow();
    expect(() => RetentionPolicySchema.parse({ snapshotsDownsampleAfterDays: 0 })).toThrow();
  });

  it('rejects non-integer days', () => {
    expect(() => RetentionPolicySchema.parse({ recordingsTTLDays: 1.5 })).toThrow();
  });

  it('passes through unknown forward-compat fields without failing', () => {
    // A future field name lands in the JSONB before this module is
    // taught about it — the sweep must not crash.
    const parsed = RetentionPolicySchema.parse({
      recordingsTTLDays: 30,
      somethingFromTheFuture: 'opaque',
    });
    expect(parsed.recordingsTTLDays).toBe(30);
  });
});

describe('parseRetentionPolicy', () => {
  it('returns defaults for null / undefined input without warning', () => {
    const warn = vi.fn<(m: string) => void>();
    expect(parseRetentionPolicy(null, warn)).toEqual(DEFAULT_RETENTION_POLICY);
    expect(parseRetentionPolicy(undefined, warn)).toEqual(DEFAULT_RETENTION_POLICY);
    // `null` is the explicit "no policy set" signal — not warn-worthy.
    expect(warn).not.toHaveBeenCalled();
  });

  it('returns defaults and emits a warning for malformed shapes', () => {
    const warn = vi.fn<(m: string) => void>();
    const parsed = parseRetentionPolicy(
      { recordingsTTLDays: 'three hundred sixty-five days' },
      warn,
    );
    expect(parsed).toEqual(DEFAULT_RETENTION_POLICY);
    expect(warn).toHaveBeenCalledOnce();
    expect(warn.mock.calls[0]?.[0]).toMatch(/retention_policy parse failed/);
  });

  it('returns defaults silently when no warn hook is given', () => {
    expect(parseRetentionPolicy({ recordingsTTLDays: -5 })).toEqual(DEFAULT_RETENTION_POLICY);
  });

  it('round-trips a valid policy intact', () => {
    const parsed = parseRetentionPolicy({
      recordingsTTLDays: 60,
      snapshotsDownsampleAfterDays: 14,
      snapshotsTTLDays: 120,
      transcriptsTTLDays: null,
    });
    expect(parsed).toEqual({
      recordingsTTLDays: 60,
      snapshotsDownsampleAfterDays: 14,
      snapshotsTTLDays: 120,
      transcriptsTTLDays: null,
    });
  });
});
