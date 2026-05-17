/**
 * State snapshots export — pure JSONL formatter.
 *
 * Each tick of the engine writes one row to `state_snapshots`. The
 * export ships one JSON object per line (newline-delimited JSON aka
 * JSONL) so consumers can `jq`-pipe or stream-parse without loading
 * the full file. This format is preferred over a wrapping array
 * because:
 *
 *   - it composes with line-oriented tools (`grep`, `head`, `jq -c`),
 *   - it lets us flush rows as we read them when we later switch to a
 *     streaming response (right now we buffer because even a dense
 *     90-min session is only ~30 MB pre-gzip),
 *   - it round-trips trivially: append a new snapshot by appending one
 *     line, no array-edit surgery required.
 *
 * Each object carries `ts`, `tick_id`, and the full SessionState blob
 * exactly as the engine persisted it. `tick_id` is serialised as a
 * JSON number (BigInt → number is safe for the bounded tick range of
 * a session: 2 Hz × 90 min = 10,800 — well under Number.MAX_SAFE_INTEGER).
 */

import type { StateSnapshotRow } from '@/features/sessions';

export interface StateSnapshotJsonl {
  ts: string;
  tick_id: number;
  state: unknown;
}

/**
 * Render one snapshot row as its JSONL line payload (object, not
 * stringified) — handy for tests that want to assert on the shape
 * without re-parsing.
 */
export function snapshotToJsonl(row: StateSnapshotRow): StateSnapshotJsonl {
  return {
    ts: row.ts.toISOString(),
    tick_id: Number(row.tickId),
    state: row.state,
  };
}

/**
 * Render a batch of snapshots as the body of the JSONL file. Each
 * object becomes one line; the result ends with a trailing newline so
 * `cat a.jsonl b.jsonl` concatenates cleanly without gluing rows.
 */
export function formatSnapshotsJsonl(rows: readonly StateSnapshotRow[]): string {
  if (rows.length === 0) return '';
  const lines = rows.map((row) => JSON.stringify(snapshotToJsonl(row)));
  return lines.join('\n') + '\n';
}
