/**
 * Tests for `selectSnapshotIdsToDropForDownsample`.
 *
 * Pins:
 *   - one row per second-bucket is kept, the rest are dropped,
 *   - empty / single-row inputs return [] (nothing to downsample),
 *   - the kept row is deterministic — sort by `(ts ASC, tickId ASC)`
 *     before picking the bucket winner, so re-running the sweep
 *     produces the same result,
 *   - input array is never mutated,
 *   - sub-second precision (e.g. 250ms vs 750ms within the same
 *     second) collapses into the same bucket.
 */

import { describe, expect, it } from 'vitest';

import {
  selectSnapshotIdsToDropForDownsample,
  type SnapshotDownsampleMeta,
} from './snapshot-downsample';

function meta(id: string, isoTs: string, tickId: bigint): SnapshotDownsampleMeta {
  return { id, ts: new Date(isoTs), tickId };
}

describe('selectSnapshotIdsToDropForDownsample', () => {
  it('returns [] for an empty input — nothing to downsample', () => {
    expect(selectSnapshotIdsToDropForDownsample([])).toEqual([]);
  });

  it('returns [] for a single row — bucket has one tenant', () => {
    expect(
      selectSnapshotIdsToDropForDownsample([meta('a', '2026-05-01T10:00:00.000Z', 1n)]),
    ).toEqual([]);
  });

  it('keeps the first row in each second-bucket and drops the rest', () => {
    // Two rows in second 0 (1st kept, 2nd dropped), two in second 1
    // (3rd kept, 4th dropped). The default 2 Hz tick rate produces
    // exactly this shape; the downsample halves the row count.
    const rows = [
      meta('a', '2026-05-01T10:00:00.000Z', 0n),
      meta('b', '2026-05-01T10:00:00.500Z', 1n),
      meta('c', '2026-05-01T10:00:01.000Z', 2n),
      meta('d', '2026-05-01T10:00:01.500Z', 3n),
    ];
    expect(selectSnapshotIdsToDropForDownsample(rows)).toEqual(['b', 'd']);
  });

  it('handles unordered input by sorting before bucketing', () => {
    // Same logical rows, scrambled — same drop set.
    const rows = [
      meta('d', '2026-05-01T10:00:01.500Z', 3n),
      meta('a', '2026-05-01T10:00:00.000Z', 0n),
      meta('c', '2026-05-01T10:00:01.000Z', 2n),
      meta('b', '2026-05-01T10:00:00.500Z', 1n),
    ];
    expect(selectSnapshotIdsToDropForDownsample(rows)).toEqual(['b', 'd']);
  });

  it('breaks ties on identical `ts` by lower `tickId`', () => {
    // Two rows at exactly the same Date but different tick ids — the
    // lower tickId wins the bucket, the higher gets dropped.
    const rows = [
      meta('higher-tick', '2026-05-01T10:00:00.000Z', 2n),
      meta('lower-tick', '2026-05-01T10:00:00.000Z', 1n),
    ];
    expect(selectSnapshotIdsToDropForDownsample(rows)).toEqual(['higher-tick']);
  });

  it('does not mutate the input array', () => {
    const rows: SnapshotDownsampleMeta[] = [
      meta('z', '2026-05-01T10:00:01.500Z', 3n),
      meta('a', '2026-05-01T10:00:00.000Z', 0n),
    ];
    const snapshot = [...rows];
    selectSnapshotIdsToDropForDownsample(rows);
    expect(rows).toEqual(snapshot);
  });

  it('collapses sub-second precision into the same bucket', () => {
    // 250ms and 750ms within the same second both belong to bucket
    // floor(ms/1000). With three rows in second 0 only the first
    // survives.
    const rows = [
      meta('a', '2026-05-01T10:00:00.000Z', 0n),
      meta('b', '2026-05-01T10:00:00.250Z', 1n),
      meta('c', '2026-05-01T10:00:00.750Z', 2n),
    ];
    expect(selectSnapshotIdsToDropForDownsample(rows)).toEqual(['b', 'c']);
  });

  it('halves a dense 60-second 2 Hz tick window — 120 rows in, 60 dropped', () => {
    const rows: SnapshotDownsampleMeta[] = [];
    const base = new Date('2026-05-01T10:00:00.000Z').getTime();
    for (let i = 0; i < 120; i++) {
      rows.push({
        id: `r-${String(i).padStart(3, '0')}`,
        ts: new Date(base + i * 500),
        tickId: BigInt(i),
      });
    }
    const drops = selectSnapshotIdsToDropForDownsample(rows);
    expect(drops).toHaveLength(60);
    // Every dropped id is the odd-indexed (500ms past the bucket
    // start) row — sanity check the bucketing.
    for (const id of drops) {
      const n = Number(id.slice(2));
      expect(n % 2).toBe(1);
    }
  });
});
