/**
 * Tests for `runRetention` — the daily orchestrator.
 *
 * Strategy: drive the runner with a fully in-memory repo + a stub
 * R2 deleter so we can pin the order of operations, the side-effect
 * counts, and the per-session error isolation without spinning up a
 * Postgres or talking to a real bucket.
 *
 * Pins:
 *   - live + young sessions are counted as "skipped" and never
 *     trigger snapshot meta reads,
 *   - downsampling chunks DELETEs at the configured chunk size,
 *   - delete-all snapshots short-circuits the downsample path,
 *   - recording deletion calls R2 with the right keys, then nulls
 *     the pointers (in that order),
 *   - one bad session is recorded but the sweep continues,
 *   - studies + null-study group are both processed.
 */

import { describe, expect, it, vi } from 'vitest';

import { DEFAULT_RETENTION_POLICY, type RetentionPolicy } from './policy';
import {
  runRetention,
  type R2KeyDeleter,
  type RetentionRepo,
  type RetentionSessionView,
  type RetentionStudyView,
  type TranscriptDeleteCounts,
} from './runner';

import type { SnapshotDownsampleMeta } from './snapshot-downsample';

const NOW = new Date('2026-05-17T10:00:00.000Z');
const DAY = 24 * 60 * 60 * 1000;
const daysAgo = (n: number): Date => new Date(NOW.getTime() - n * DAY);

interface FakeSnapshot {
  id: string;
  ts: Date;
  tickId: bigint;
}

interface FakeStudy {
  studyId: string | null;
  policy: RetentionPolicy;
  sessions: RetentionSessionView[];
}

function session(overrides: Partial<RetentionSessionView> = {}): RetentionSessionView {
  return {
    id: 'sess-default',
    actualEnd: daysAgo(1),
    recordingUrl: null,
    perParticipantRecordingUrls: {},
    ...overrides,
  };
}

function asAsyncIterable<T>(items: readonly T[]): AsyncIterable<T> {
  // Trivial async iterator over an in-memory list; the runner only needs
  // `for await ... of` semantics, not a real awaitable source.
  return {
    [Symbol.asyncIterator]() {
      let i = 0;
      return {
        next(): Promise<IteratorResult<T>> {
          if (i >= items.length) {
            return Promise.resolve({ value: undefined, done: true });
          }
          const value = items[i] as T;
          i += 1;
          return Promise.resolve({ value, done: false });
        },
      };
    },
  };
}

interface FakeRepoState {
  studies: FakeStudy[];
  snapshotMetaBySession: Record<string, FakeSnapshot[]>;
}

interface FakeRepoTrace {
  listSnapshotMetaCalls: string[];
  deleteSnapshotsByIdsCalls: { sessionContext: string | null; ids: readonly string[] }[];
  deleteAllSnapshotsCalls: string[];
  deleteTranscriptsCalls: string[];
  clearRecordingPointersCalls: string[];
}

interface FakeRepoBundle {
  repo: RetentionRepo;
  trace: FakeRepoTrace;
}

function makeRepo(state: FakeRepoState): FakeRepoBundle {
  const trace: FakeRepoTrace = {
    listSnapshotMetaCalls: [],
    deleteSnapshotsByIdsCalls: [],
    deleteAllSnapshotsCalls: [],
    deleteTranscriptsCalls: [],
    clearRecordingPointersCalls: [],
  };
  const repo: RetentionRepo = {
    iterateStudies(): AsyncIterable<RetentionStudyView> {
      return asAsyncIterable(
        state.studies.map((s) => ({
          studyId: s.studyId,
          policy: s.policy,
          sessions: asAsyncIterable(s.sessions),
        })),
      );
    },
    listSnapshotMeta(sessionId: string): Promise<SnapshotDownsampleMeta[]> {
      trace.listSnapshotMetaCalls.push(sessionId);
      return Promise.resolve(state.snapshotMetaBySession[sessionId] ?? []);
    },
    deleteSnapshotsByIds(ids: readonly string[]): Promise<number> {
      trace.deleteSnapshotsByIdsCalls.push({ sessionContext: null, ids });
      return Promise.resolve(ids.length);
    },
    deleteAllSnapshotsForSession(sessionId: string): Promise<number> {
      trace.deleteAllSnapshotsCalls.push(sessionId);
      return Promise.resolve((state.snapshotMetaBySession[sessionId] ?? []).length);
    },
    deleteTranscriptsForSession(sessionId: string): Promise<TranscriptDeleteCounts> {
      trace.deleteTranscriptsCalls.push(sessionId);
      return Promise.resolve({ utterances: 3, decisions: 5, flags: 1, actions: 2 });
    },
    clearRecordingPointers(sessionId: string): Promise<void> {
      trace.clearRecordingPointersCalls.push(sessionId);
      return Promise.resolve();
    },
  };
  return { repo, trace };
}

function makeR2(): { r2: R2KeyDeleter; calls: string[][] } {
  const calls: string[][] = [];
  const r2: R2KeyDeleter = {
    deleteKeys(keys: readonly string[]): Promise<{ deleted: number; errors: string[] }> {
      calls.push([...keys]);
      return Promise.resolve({ deleted: keys.length, errors: [] });
    },
  };
  return { r2, calls };
}

describe('runRetention', () => {
  it('skips live sessions without ever asking for snapshot meta', async () => {
    const { repo, trace } = makeRepo({
      studies: [
        {
          studyId: 's-1',
          policy: DEFAULT_RETENTION_POLICY,
          sessions: [session({ id: 'live-1', actualEnd: null })],
        },
      ],
      snapshotMetaBySession: {},
    });
    const { r2 } = makeR2();
    const result = await runRetention(repo, r2, { now: NOW });
    expect(result.studiesProcessed).toBe(1);
    expect(result.sessionsProcessed).toBe(0);
    expect(result.sessionsSkipped).toBe(1);
    expect(trace.listSnapshotMetaCalls).toEqual([]);
  });

  it('skips young sessions (under all thresholds)', async () => {
    const { repo, trace } = makeRepo({
      studies: [
        {
          studyId: 's-1',
          policy: DEFAULT_RETENTION_POLICY,
          sessions: [
            session({ id: 'young-1', actualEnd: daysAgo(5) }),
            session({ id: 'young-2', actualEnd: daysAgo(10) }),
          ],
        },
      ],
      snapshotMetaBySession: {},
    });
    const { r2 } = makeR2();
    const result = await runRetention(repo, r2, { now: NOW });
    expect(result.sessionsSkipped).toBe(2);
    expect(trace.listSnapshotMetaCalls).toEqual([]);
  });

  it('downsamples a session past the 30-day window in chunks', async () => {
    // Build 6 snapshots, two per second: 3 should drop.
    const base = new Date('2026-04-10T10:00:00.000Z').getTime();
    const snaps: FakeSnapshot[] = [
      { id: 's-a', ts: new Date(base + 0), tickId: 0n },
      { id: 's-b', ts: new Date(base + 500), tickId: 1n },
      { id: 's-c', ts: new Date(base + 1000), tickId: 2n },
      { id: 's-d', ts: new Date(base + 1500), tickId: 3n },
      { id: 's-e', ts: new Date(base + 2000), tickId: 4n },
      { id: 's-f', ts: new Date(base + 2500), tickId: 5n },
    ];
    const { repo, trace } = makeRepo({
      studies: [
        {
          studyId: 's-1',
          policy: DEFAULT_RETENTION_POLICY,
          sessions: [session({ id: 'old-1', actualEnd: daysAgo(40) })],
        },
      ],
      snapshotMetaBySession: { 'old-1': snaps },
    });
    const { r2 } = makeR2();
    // Force a chunk size of 2 to verify chunking, even with only 3 ids.
    const result = await runRetention(repo, r2, { now: NOW, snapshotDeleteChunkSize: 2 });
    expect(result.snapshotsDeleted).toBe(3);
    expect(trace.listSnapshotMetaCalls).toEqual(['old-1']);
    // Two DELETEs: first chunk of 2, second chunk of 1.
    expect(trace.deleteSnapshotsByIdsCalls).toHaveLength(2);
    expect(trace.deleteSnapshotsByIdsCalls[0]?.ids).toEqual(['s-b', 's-d']);
    expect(trace.deleteSnapshotsByIdsCalls[1]?.ids).toEqual(['s-f']);
    expect(trace.deleteAllSnapshotsCalls).toEqual([]);
  });

  it('short-circuits to delete-all snapshots past snapshot TTL — no downsample first', async () => {
    const { repo, trace } = makeRepo({
      studies: [
        {
          studyId: 's-1',
          policy: {
            ...DEFAULT_RETENTION_POLICY,
            snapshotsDownsampleAfterDays: 30,
            snapshotsTTLDays: 90,
          },
          sessions: [session({ id: 'ancient-1', actualEnd: daysAgo(120) })],
        },
      ],
      snapshotMetaBySession: { 'ancient-1': [{ id: 'x', ts: NOW, tickId: 0n }] },
    });
    const { r2 } = makeR2();
    const result = await runRetention(repo, r2, { now: NOW });
    expect(trace.deleteAllSnapshotsCalls).toEqual(['ancient-1']);
    expect(trace.listSnapshotMetaCalls).toEqual([]);
    expect(trace.deleteSnapshotsByIdsCalls).toEqual([]);
    expect(result.snapshotsDeleted).toBe(1);
  });

  it('deletes R2 recordings then nulls the pointers — never the other way round', async () => {
    const order: string[] = [];
    const { repo } = makeRepo({
      studies: [
        {
          studyId: 's-1',
          policy: DEFAULT_RETENTION_POLICY,
          sessions: [
            session({
              id: 'oldrec-1',
              actualEnd: daysAgo(400),
              recordingUrl: 'sessions/oldrec-1/composite.mp4',
              perParticipantRecordingUrls: {
                'identity-a': 'sessions/oldrec-1/tracks/a.opus',
                'identity-b': 'sessions/oldrec-1/tracks/b.opus',
              },
            }),
          ],
        },
      ],
      snapshotMetaBySession: { 'oldrec-1': [] },
    });
    const r2: R2KeyDeleter = {
      deleteKeys(keys): Promise<{ deleted: number; errors: string[] }> {
        order.push(`r2:${keys.join(',')}`);
        return Promise.resolve({ deleted: keys.length, errors: [] });
      },
    };
    // Spy on clearRecordingPointers to insert into the order trace.
    const original = repo.clearRecordingPointers;
    repo.clearRecordingPointers = async (sessionId: string): Promise<void> => {
      order.push(`clear:${sessionId}`);
      await original.call(repo, sessionId);
    };
    const result = await runRetention(repo, r2, { now: NOW });
    expect(result.recordingsDeleted).toBe(3);
    expect(order).toEqual([
      'r2:sessions/oldrec-1/composite.mp4,sessions/oldrec-1/tracks/a.opus,sessions/oldrec-1/tracks/b.opus',
      'clear:oldrec-1',
    ]);
  });

  it('skips R2 delete + pointer clear when there is no recording', async () => {
    const { repo, trace } = makeRepo({
      studies: [
        {
          studyId: 's-1',
          policy: DEFAULT_RETENTION_POLICY,
          // Past downsample window but no recording → only downsample fires.
          sessions: [session({ id: 'norec', actualEnd: daysAgo(40), recordingUrl: null })],
        },
      ],
      snapshotMetaBySession: { norec: [] },
    });
    const { r2, calls } = makeR2();
    const result = await runRetention(repo, r2, { now: NOW });
    expect(calls).toEqual([]);
    expect(trace.clearRecordingPointersCalls).toEqual([]);
    expect(result.recordingsDeleted).toBe(0);
  });

  it('isolates per-session failures — one bad session does not stop the sweep', async () => {
    const { repo } = makeRepo({
      studies: [
        {
          studyId: 's-1',
          policy: DEFAULT_RETENTION_POLICY,
          sessions: [
            session({ id: 'bad', actualEnd: daysAgo(40) }),
            session({ id: 'good', actualEnd: daysAgo(40) }),
          ],
        },
      ],
      snapshotMetaBySession: { good: [] },
    });
    // First listSnapshotMeta call (for "bad") throws.
    let callCount = 0;
    const originalList = repo.listSnapshotMeta;
    repo.listSnapshotMeta = async (sessionId: string): Promise<SnapshotDownsampleMeta[]> => {
      callCount += 1;
      if (callCount === 1) throw new Error('boom');
      return originalList.call(repo, sessionId);
    };
    const { r2 } = makeR2();
    const result = await runRetention(repo, r2, { now: NOW });
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]?.sessionId).toBe('bad');
    expect(result.errors[0]?.studyId).toBe('s-1');
    expect(result.errors[0]?.message).toBe('boom');
    expect(result.sessionsProcessed).toBe(2);
  });

  it('processes the null-study group alongside named studies', async () => {
    const { repo, trace } = makeRepo({
      studies: [
        {
          studyId: 's-1',
          policy: DEFAULT_RETENTION_POLICY,
          sessions: [session({ id: 'with-study', actualEnd: daysAgo(40) })],
        },
        {
          studyId: null,
          policy: DEFAULT_RETENTION_POLICY,
          sessions: [session({ id: 'no-study', actualEnd: daysAgo(40) })],
        },
      ],
      snapshotMetaBySession: { 'with-study': [], 'no-study': [] },
    });
    const { r2 } = makeR2();
    const result = await runRetention(repo, r2, { now: NOW });
    expect(result.studiesProcessed).toBe(2);
    expect(trace.listSnapshotMetaCalls).toEqual(['with-study', 'no-study']);
  });

  it('deletes transcripts when transcripts TTL is reached and counts each table', async () => {
    const { repo, trace } = makeRepo({
      studies: [
        {
          studyId: 's-1',
          policy: { ...DEFAULT_RETENTION_POLICY, transcriptsTTLDays: 365 },
          sessions: [session({ id: 'ancient-trx', actualEnd: daysAgo(400) })],
        },
      ],
      snapshotMetaBySession: { 'ancient-trx': [] },
    });
    const { r2 } = makeR2();
    const result = await runRetention(repo, r2, { now: NOW });
    expect(trace.deleteTranscriptsCalls).toEqual(['ancient-trx']);
    // Repo fixture: 3 utt + 5 dec + 1 flag + 2 act = 11.
    expect(result.transcriptsDeleted).toBe(11);
  });

  it('calls the logger with structured events on completion', async () => {
    const logger = {
      info: vi.fn<(m: string, f?: Record<string, unknown>) => void>(),
      warn: vi.fn<(m: string, f?: Record<string, unknown>) => void>(),
      error: vi.fn<(m: string, f?: Record<string, unknown>) => void>(),
    };
    const { repo } = makeRepo({
      studies: [
        {
          studyId: 's-1',
          policy: DEFAULT_RETENTION_POLICY,
          sessions: [session({ id: 'one', actualEnd: daysAgo(40) })],
        },
      ],
      snapshotMetaBySession: { one: [] },
    });
    const { r2 } = makeR2();
    await runRetention(repo, r2, { now: NOW, logger });
    const events = logger.info.mock.calls.map((c) => c[0]);
    expect(events).toContain('retention.study.start');
    expect(events).toContain('retention.run.complete');
  });
});
