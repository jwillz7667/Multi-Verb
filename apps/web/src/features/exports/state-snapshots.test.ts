/**
 * Tests for the state snapshots JSONL formatter.
 *
 * Coverage:
 *   - One line per snapshot, no surrounding array.
 *   - `tick_id` BigInt → JSON number; `ts` ISO string; `state` passed
 *     through unmodified.
 *   - Each line is itself valid JSON (round-trip via JSON.parse).
 *   - Empty input → empty string (no spurious newline).
 *   - Non-empty input → trailing newline for clean concatenation.
 *   - Nested + array `state` values serialise without losing fidelity.
 */
import { describe, expect, it } from 'vitest';

import type { StateSnapshotRow } from '@/features/sessions';

import { formatSnapshotsJsonl, snapshotToJsonl } from './state-snapshots';

function makeSnapshot(overrides: Partial<StateSnapshotRow>): StateSnapshotRow {
  return {
    id: 's-default',
    sessionId: 'sess-1',
    tickId: 0n,
    ts: new Date('2026-05-01T10:00:00.000Z'),
    state: { participants: {}, time_remaining_sec: 3600 },
    ...overrides,
  };
}

describe('snapshotToJsonl', () => {
  it('converts BigInt tick_id to Number and Date to ISO', () => {
    const out = snapshotToJsonl(
      makeSnapshot({
        tickId: 17n,
        ts: new Date('2026-05-01T10:00:08.500Z'),
        state: { foo: 'bar' },
      }),
    );
    expect(out).toEqual({
      ts: '2026-05-01T10:00:08.500Z',
      tick_id: 17,
      state: { foo: 'bar' },
    });
  });

  it('passes the state blob through unmodified for nested + array shapes', () => {
    const state = {
      participants: { 'p-alice': { last_spoken_ts: '2026-05-01T10:00:00.000Z', wpm: 110 } },
      flags: [{ code: 'cross_talk', triggered_at: '2026-05-01T10:00:05.000Z' }],
    };
    const out = snapshotToJsonl(makeSnapshot({ state }));
    expect(out.state).toEqual(state);
  });
});

describe('formatSnapshotsJsonl', () => {
  it('emits one JSON object per line, terminated with a single newline', () => {
    const out = formatSnapshotsJsonl([
      makeSnapshot({ id: 's-1', tickId: 0n }),
      makeSnapshot({ id: 's-2', tickId: 1n }),
      makeSnapshot({ id: 's-3', tickId: 2n }),
    ]);
    expect(out.endsWith('\n')).toBe(true);
    const lines = out.trimEnd().split('\n');
    expect(lines).toHaveLength(3);
    // Each line is independently parseable — the JSONL contract.
    for (const line of lines) {
      expect(() => {
        JSON.parse(line);
      }).not.toThrow();
    }
  });

  it('preserves declared input order (snapshots are already ts-sorted upstream)', () => {
    const out = formatSnapshotsJsonl([
      makeSnapshot({ id: 's-a', tickId: 5n, ts: new Date('2026-05-01T10:00:02.500Z') }),
      makeSnapshot({ id: 's-b', tickId: 6n, ts: new Date('2026-05-01T10:00:03.000Z') }),
    ]);
    const lines = out.trimEnd().split('\n');
    const parsedA = JSON.parse(lines[0] ?? '') as { tick_id: number };
    const parsedB = JSON.parse(lines[1] ?? '') as { tick_id: number };
    expect(parsedA.tick_id).toBe(5);
    expect(parsedB.tick_id).toBe(6);
  });

  it('returns an empty string for an empty input — no stray newline', () => {
    expect(formatSnapshotsJsonl([])).toBe('');
  });

  it('embeds the full state blob verbatim per row', () => {
    const out = formatSnapshotsJsonl([
      makeSnapshot({
        tickId: 12n,
        state: { silence_gap_sec: 45.5, last_speaker_id: 'p-alice' },
      }),
    ]);
    const parsed = JSON.parse(out.trimEnd()) as {
      tick_id: number;
      state: { silence_gap_sec: number; last_speaker_id: string };
    };
    expect(parsed.tick_id).toBe(12);
    expect(parsed.state).toEqual({ silence_gap_sec: 45.5, last_speaker_id: 'p-alice' });
  });
});
