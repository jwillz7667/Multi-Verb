/**
 * Pure helper — choose which `state_snapshots` rows to drop to
 * downsample a session from its native 2 Hz tick rate down to 1 Hz.
 *
 * The engine writes one row per tick (≈ 7,200 rows per 60-min session
 * at 2 Hz). The brief (§10.2) calls for that to compact to 1 Hz once a
 * session is old enough that the dense replay window has passed —
 * trend-level analysis only needs one sample per second.
 *
 * Bucketing: row timestamps are floored to integer seconds (UTC). The
 * *first* row in each second-bucket is kept; every subsequent row in
 * the same bucket is dropped. "First" is defined by `(ts ASC, tickId
 * ASC)` so the choice is deterministic across re-runs of the sweep,
 * and matches the index order the snapshot range queries already use.
 *
 * Why pure-TS rather than a single SQL `DELETE … WHERE id NOT IN (…)`:
 *   - the SQL form sweeps every row of a partition with a window
 *     function, then takes a self-join lock pattern that nests
 *     surprisingly badly on Postgres for large `state_snapshots`,
 *   - the bucketing logic is fiddly enough that it deserves a unit
 *     test independent of SQL semantics + Prisma idiosyncrasies,
 *   - chunked deletes (a few hundred ids per round-trip) are easier
 *     to interrupt, log, and reason about than one giant DELETE.
 *
 * The cost is two round-trips per session being downsampled (SELECT
 * meta, then DELETE batched). At our session volume that's negligible
 * compared to the storage saved.
 */

export interface SnapshotDownsampleMeta {
  /** Row id — used to issue the DELETE. */
  id: string;
  /** Snapshot timestamp; buckets at second granularity. */
  ts: Date;
  /**
   * Engine-assigned monotonic tick id. Used as the deterministic
   * tiebreaker when two rows share a second-bucket (two snapshots can
   * land in the same second at 2 Hz). Kept as `bigint` to match the
   * Prisma column type and avoid silent precision loss.
   */
  tickId: bigint;
}

/**
 * Return the ids of rows that should be DELETEd to leave one snapshot
 * per second-bucket. Rows kept are NOT returned — callers usually only
 * need the drop list to feed into `deleteMany({ where: { id: { in: …
 * }}})`.
 *
 * Stable ordering: input is sorted by `(ts ASC, tickId ASC)` before
 * bucketing, so the helper is deterministic even when the upstream
 * query returned rows in an unrelated order.
 *
 * Empty / single-row input returns an empty array — there's nothing to
 * downsample if there's at most one tick.
 */
export function selectSnapshotIdsToDropForDownsample(
  rows: readonly SnapshotDownsampleMeta[],
): string[] {
  if (rows.length <= 1) return [];

  // Copy + sort — never mutate the caller's array.
  const ordered = [...rows].sort((a, b) => {
    const tsDiff = a.ts.getTime() - b.ts.getTime();
    if (tsDiff !== 0) return tsDiff;
    if (a.tickId < b.tickId) return -1;
    if (a.tickId > b.tickId) return 1;
    return 0;
  });

  const seenBuckets = new Set<number>();
  const drop: string[] = [];
  for (const row of ordered) {
    // `Math.floor(ms / 1000)` is the UTC-second bucket. We don't care
    // about local time zones — snapshots are timestamps, not wall
    // clocks, and the engine writes everything in UTC.
    const bucket = Math.floor(row.ts.getTime() / 1000);
    if (seenBuckets.has(bucket)) {
      drop.push(row.id);
    } else {
      seenBuckets.add(bucket);
    }
  }
  return drop;
}
