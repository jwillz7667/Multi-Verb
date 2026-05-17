'use client';

/**
 * Replay state snapshot panel — shows the SessionState frozen at the
 * scrubber position (brief §11.2).
 *
 * Data sources:
 *
 *   1. Bootstrap snapshots from the server component (5 minutes ≈ 600
 *      rows). On first paint the scrubber sits on `actual_start`, so
 *      the bootstrap covers the most common research entry — drop into
 *      the session start, scrub forward.
 *   2. On-demand fetch from GET /api/sessions/[id]/snapshots/at?ts=…
 *      when the scrubber moves outside the bootstrap range. Each fetch
 *      returns one row; we memoize by ts so scrubbing back doesn't
 *      re-request the same row.
 *
 * Selection rule (matches the panel's audit semantics): the rendered
 * snapshot is the freshest one at or before `currentTs`. If the
 * scrubber lands before the first tick we render a "no data yet"
 * affordance — better than picking a future tick the researcher
 * hadn't witnessed at that scrubber moment.
 *
 * The renderer mirrors `<ParticipantTiles />` in shape (session
 * counters + per-participant table) so a researcher who's used to the
 * live page recognizes the panel without retraining. The differences
 * are visual chrome (replay-mode background) and the fact that we
 * read from a frozen snapshot instead of an SSE stream.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { ParticipantState, SessionState } from '@verbio/shared-types';

import type { ReplayParticipantDTO, ReplaySnapshotDTO } from './replay-types';

// `ParticipantFlags` isn't re-exported from the @verbio/shared-types
// barrel today; pulling it out of `ParticipantState` keeps the panel
// in sync with the generated schema without a deep import.
type ParticipantFlags = NonNullable<ParticipantState['flags']>;

interface Props {
  sessionId: string;
  participants: ReplayParticipantDTO[];
  initialSnapshots: ReplaySnapshotDTO[];
  currentTs: string;
}

type FetchState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'error'; code: 'no_snapshot' | 'network' | 'unauthorized' };

export function ReplayStateSnapshot({
  sessionId,
  participants,
  initialSnapshots,
  currentTs,
}: Props): React.ReactElement {
  // Cache of every snapshot we've ever seen — keyed by id so a
  // re-fetch returning a row we already have is a no-op. The
  // bootstrap rows seed the cache.
  const [snapshotCache, setSnapshotCache] = useState<ReadonlyMap<string, ReplaySnapshotDTO>>(
    seedCache(initialSnapshots),
  );
  const [fetchState, setFetchState] = useState<FetchState>({ kind: 'idle' });

  // Track which ts values we've already requested so a rapid scrub
  // doesn't dispatch the same request twice in flight. Also tracks
  // negative results (404s) so we don't loop on the "before first
  // tick" case.
  const inflightTsRef = useRef<Set<string>>(new Set());
  const negativeCacheRef = useRef<Set<string>>(new Set());

  // The active snapshot — the freshest cache entry ≤ currentTs. The
  // useMemo recomputes whenever the cache changes (new fetch) or the
  // scrubber moves.
  const activeSnapshot = useMemo(
    () => findActiveSnapshot(snapshotCache, currentTs),
    [snapshotCache, currentTs],
  );

  const fetchSnapshotAt = useCallback(
    async (ts: string): Promise<void> => {
      if (inflightTsRef.current.has(ts)) return;
      if (negativeCacheRef.current.has(ts)) return;
      inflightTsRef.current.add(ts);
      setFetchState({ kind: 'loading' });
      try {
        const res = await fetch(
          `/api/sessions/${sessionId}/snapshots/at?ts=${encodeURIComponent(ts)}`,
        );
        if (res.status === 200) {
          const body = (await res.json()) as ReplaySnapshotDTO;
          setSnapshotCache((prev) => {
            const next = new Map(prev);
            next.set(body.id, body);
            return next;
          });
          setFetchState({ kind: 'idle' });
          return;
        }
        if (res.status === 404) {
          negativeCacheRef.current.add(ts);
          setFetchState({ kind: 'error', code: 'no_snapshot' });
          return;
        }
        if (res.status === 401) {
          setFetchState({ kind: 'error', code: 'unauthorized' });
          return;
        }
        setFetchState({ kind: 'error', code: 'network' });
      } catch {
        setFetchState({ kind: 'error', code: 'network' });
      } finally {
        inflightTsRef.current.delete(ts);
      }
    },
    [sessionId],
  );

  // When the scrubber moves and we have nothing useful in cache, ask
  // for the snapshot at that ts. Re-runs whenever the cache or
  // scrubber changes, but the fetch dedupe in `fetchSnapshotAt`
  // prevents request floods.
  useEffect(() => {
    if (activeSnapshot !== null) {
      if (fetchState.kind !== 'idle') setFetchState({ kind: 'idle' });
      return;
    }
    if (negativeCacheRef.current.has(currentTs)) return;
    void fetchSnapshotAt(currentTs);
  }, [activeSnapshot, currentTs, fetchSnapshotAt, fetchState.kind]);

  // Build a name-sorted display of participants from the active
  // snapshot. Fall back to bootstrap participants when the snapshot
  // hasn't populated yet — gives the researcher the roster shape
  // even before any tick lands.
  const renderedParticipants = useMemo(() => {
    const state = parseSessionState(activeSnapshot);
    if (state !== null) {
      const map = state.participants ?? {};
      return Object.values(map)
        .filter((p): p is ParticipantState => p !== undefined)
        .sort((a, b) => a.display_name.localeCompare(b.display_name));
    }
    return [];
  }, [activeSnapshot]);

  const state = parseSessionState(activeSnapshot);

  return (
    <section
      className="border-border-default bg-surface-primary flex min-h-[20rem] flex-col gap-3 rounded-lg border p-6"
      data-testid="replay-state-slot"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-text-primary text-sm font-medium uppercase tracking-wide">
          State snapshot
        </h2>
        <p className="text-text-tertiary font-mono text-xs" data-testid="replay-state-active-ts">
          {activeSnapshot !== null ? `@ ${activeSnapshot.ts}` : `@ ${currentTs}`}
        </p>
      </div>

      {activeSnapshot === null ? (
        <EmptyState
          fetchState={fetchState}
          hasParticipants={participants.length > 0}
          currentTs={currentTs}
        />
      ) : (
        <>
          {state !== null && (
            <dl
              className="text-text-secondary grid grid-cols-3 gap-3 text-xs"
              data-testid="replay-state-counters"
            >
              <div className="flex flex-col">
                <dt className="text-text-tertiary uppercase tracking-wide">Speaking now</dt>
                <dd className="text-text-primary font-mono">
                  {state.currently_speaking_count ?? 0}
                </dd>
              </div>
              <div className="flex flex-col">
                <dt className="text-text-tertiary uppercase tracking-wide">Silence</dt>
                <dd className="text-text-primary font-mono">
                  {formatSeconds(state.silence_run_sec ?? 0)}
                </dd>
              </div>
              <div className="flex flex-col">
                <dt className="text-text-tertiary uppercase tracking-wide">Elapsed</dt>
                <dd className="text-text-primary font-mono">{formatSeconds(state.elapsed_sec)}</dd>
              </div>
              <div className="flex flex-col">
                <dt className="text-text-tertiary uppercase tracking-wide">Tick</dt>
                <dd className="text-text-primary font-mono">{activeSnapshot.tick_id}</dd>
              </div>
              <div className="flex flex-col">
                <dt className="text-text-tertiary uppercase tracking-wide">Budget used</dt>
                <dd className="text-text-primary font-mono">
                  {formatBudgetUsed(state.quietness_budget)}
                </dd>
              </div>
              <div className="flex flex-col">
                <dt className="text-text-tertiary uppercase tracking-wide">Pause / mute</dt>
                <dd className="text-text-primary font-mono">{formatPauseMute(state)}</dd>
              </div>
            </dl>
          )}

          {renderedParticipants.length === 0 ? (
            <p className="text-text-tertiary text-sm italic">No participants in the snapshot.</p>
          ) : (
            <ul
              className="grid grid-cols-1 gap-3 md:grid-cols-2"
              data-testid="replay-state-participants"
            >
              {renderedParticipants.map((p) => (
                <li
                  key={p.participant_id}
                  className="border-border-default bg-surface-secondary flex flex-col gap-2 rounded-md border p-3"
                  data-testid={`replay-state-participant-${p.participant_id}`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-text-primary font-medium">{p.display_name}</span>
                    <SpeakingBadge
                      isSpeaking={p.is_currently_speaking ?? false}
                      vadActive={p.vad_active ?? false}
                    />
                  </div>
                  <dl className="text-text-secondary grid grid-cols-2 gap-y-1 text-xs">
                    <dt className="text-text-tertiary">Spoken</dt>
                    <dd className="text-text-primary font-mono">
                      {formatSeconds(p.speaking_time_total_sec ?? 0)}
                    </dd>
                    <dt className="text-text-tertiary">Turns</dt>
                    <dd className="text-text-primary font-mono">{p.turn_count ?? 0}</dd>
                    <dt className="text-text-tertiary">Share (5m)</dt>
                    <dd className="text-text-primary font-mono">
                      {(p.actual_share_last_5min_pct ?? 0).toFixed(0)}% / fair{' '}
                      {(p.fair_share_pct ?? 0).toFixed(0)}%
                    </dd>
                    <dt className="text-text-tertiary">Last spoke</dt>
                    <dd className="text-text-primary font-mono">
                      {formatAbsoluteTime(p.last_spoke_at ?? null)}
                    </dd>
                  </dl>
                  <FlagsRow flags={p.flags} />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

function EmptyState({
  fetchState,
  hasParticipants,
  currentTs,
}: {
  fetchState: FetchState;
  hasParticipants: boolean;
  currentTs: string;
}): React.ReactElement {
  if (fetchState.kind === 'loading') {
    return (
      <p className="text-text-tertiary text-sm italic" data-testid="replay-state-loading">
        Loading snapshot at {currentTs}…
      </p>
    );
  }
  if (fetchState.kind === 'error') {
    return (
      <p className="text-text-tertiary text-sm" data-testid="replay-state-error">
        {fetchState.code === 'no_snapshot' && 'No snapshot at this time — try scrubbing forward.'}
        {fetchState.code === 'unauthorized' && 'Sign in again to view snapshots.'}
        {fetchState.code === 'network' && 'Snapshot fetch failed — retry by re-scrubbing.'}
      </p>
    );
  }
  return (
    <p className="text-text-tertiary text-sm italic">
      {hasParticipants
        ? 'Waiting for snapshot data…'
        : 'No snapshots persisted for this session yet.'}
    </p>
  );
}

function SpeakingBadge({
  isSpeaking,
  vadActive,
}: {
  isSpeaking: boolean;
  vadActive: boolean;
}): React.ReactElement {
  if (isSpeaking) {
    return (
      <span className="flex items-center gap-1 text-xs font-medium text-green-700 dark:text-green-400">
        <span className="inline-block h-2 w-2 rounded-full bg-green-500" />
        Speaking
      </span>
    );
  }
  if (vadActive) {
    return (
      <span className="text-text-secondary flex items-center gap-1 text-xs">
        <span className="inline-block h-2 w-2 rounded-full bg-yellow-400" />
        VAD
      </span>
    );
  }
  return (
    <span className="text-text-tertiary flex items-center gap-1 text-xs">
      <span className="inline-block h-2 w-2 rounded-full bg-gray-300 dark:bg-gray-600" />
      Idle
    </span>
  );
}

function FlagsRow({ flags }: { flags: ParticipantFlags | undefined }): React.ReactElement | null {
  if (flags === undefined) return null;
  const active: string[] = [];
  if (flags.dominating === true) active.push('dominating');
  if (flags.silent_too_long === true) active.push('silent_too_long');
  if (flags.frequently_interrupted === true) active.push('frequently_interrupted');
  if (flags.disengaged === true) active.push('disengaged');
  if (active.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1">
      {active.map((flag) => (
        <li
          key={flag}
          className="border-border-default text-text-secondary rounded-sm border bg-amber-100/30 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide dark:bg-amber-900/30"
        >
          {flag.replace(/_/g, ' ')}
        </li>
      ))}
    </ul>
  );
}

function seedCache(snapshots: ReplaySnapshotDTO[]): ReadonlyMap<string, ReplaySnapshotDTO> {
  return new Map(snapshots.map((s) => [s.id, s]));
}

function findActiveSnapshot(
  cache: ReadonlyMap<string, ReplaySnapshotDTO>,
  currentTs: string,
): ReplaySnapshotDTO | null {
  const cutoff = Date.parse(currentTs);
  if (Number.isNaN(cutoff)) return null;
  let best: ReplaySnapshotDTO | null = null;
  let bestMs = -Infinity;
  for (const snap of cache.values()) {
    const snapMs = Date.parse(snap.ts);
    if (Number.isNaN(snapMs) || snapMs > cutoff) continue;
    if (snapMs > bestMs) {
      best = snap;
      bestMs = snapMs;
    }
  }
  return best;
}

function parseSessionState(snapshot: ReplaySnapshotDTO | null): SessionState | null {
  if (snapshot === null) return null;
  const state = snapshot.state;
  if (typeof state !== 'object' || state === null) return null;
  // The shape is enforced server-side at write time (Pydantic on the
  // engine, Zod on the SSE relay) — the panel reads it back without
  // re-validating per render, matching how `<ParticipantTiles>` works.
  return state as SessionState;
}

function formatSeconds(value: number): string {
  if (!Number.isFinite(value)) return '—';
  if (value < 60) return `${value.toFixed(1)}s`;
  const mins = Math.floor(value / 60);
  const secs = Math.round(value - mins * 60);
  return `${mins.toString()}m ${secs.toString().padStart(2, '0')}s`;
}

function formatAbsoluteTime(iso: string | null): string {
  if (iso === null) return 'never';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatBudgetUsed(budget: SessionState['quietness_budget']): string {
  if (budget === undefined) return '—';
  const used = budget.current_window_count ?? 0;
  const max = budget.max_utterances_per_10min ?? 0;
  if (max === 0) return used.toString();
  return `${used.toString()} / ${max.toString()}`;
}

function formatPauseMute(state: SessionState): string {
  const paused = state.is_paused === true;
  const muted = state.moderator_muted === true;
  if (paused && muted) return 'paused + muted';
  if (paused) return 'paused';
  if (muted) return 'muted';
  return 'active';
}
