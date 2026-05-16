'use client';

/**
 * ParticipantTiles — researcher-facing live participant view.
 *
 * Subscribes to the same `/api/sessions/[id]/events` stream as
 * `<LiveTranscript>` but listens for the `state_snapshot` variant
 * only. Each snapshot is the full `SessionState` projected at one
 * tick, so the component renders directly from the freshest snapshot
 * — no rolling state on the client side, no diff math.
 *
 * Phase 2 surface (brief §14): per-participant speaking time, last
 * spoke, currently-speaking + VAD indicators, derived flags
 * (dominating, silent_too_long, frequently_interrupted, disengaged),
 * and session-level tiles for silence run + speaker count.
 *
 * Why a separate component (not folded into `<LiveTranscript>`): the
 * transcript is unbounded append-only; the tile grid is a single
 * always-fresh snapshot. Different cadences, different state shapes,
 * different visual rhythm. Co-mounting them on the page keeps the
 * dashboard layout flexible — researchers can move them
 * independently in later phases.
 *
 * Both components open their own `EventSource`. Today that's two TCP
 * subscriptions to the same Redis channel; if dashboard density grows,
 * Phase 6 introduces a context provider that hosts one stream and
 * fans out via React context. For now, two streams stays cheap and
 * keeps the components self-contained.
 */

import { useEffect, useMemo, useState } from 'react';

import {
  parseTranscriptEvent,
  type StateSnapshotTranscriptEvent,
} from '@/features/sessions/events';

interface Props {
  sessionId: string;
}

type ConnectionState = 'connecting' | 'open' | 'closed';

type SessionState = StateSnapshotTranscriptEvent['payload']['state'];
type ParticipantState = NonNullable<SessionState['participants']>[string];

export function ParticipantTiles({ sessionId }: Props): React.ReactElement {
  const [state, setState] = useState<SessionState | null>(null);
  const [connection, setConnection] = useState<ConnectionState>('connecting');

  useEffect(() => {
    const source = new EventSource(`/api/sessions/${sessionId}/events`);

    const onReady = (): void => {
      setConnection('open');
    };

    const onSnapshot = (raw: MessageEvent<string>): void => {
      const parsed = parseTranscriptEvent(safeJsonParse(raw.data));
      if (parsed?.type !== 'state_snapshot') return;
      // Each snapshot is self-contained; we keep only the freshest one.
      // Out-of-order arrivals are possible on backfill (engine emits
      // ascending tick_id, but the browser's event loop can interleave),
      // so guard with a tick_id comparator rather than blindly overwriting.
      setState((prev) =>
        prev === null || parsed.payload.state.tick_id >= prev.tick_id ? parsed.payload.state : prev,
      );
    };

    const onOpen = (): void => {
      setConnection('open');
    };

    const onError = (): void => {
      setConnection(source.readyState === EventSource.CLOSED ? 'closed' : 'connecting');
    };

    source.addEventListener('ready', onReady);
    source.addEventListener('state_snapshot', onSnapshot);
    source.addEventListener('open', onOpen);
    source.addEventListener('error', onError);

    return () => {
      source.removeEventListener('ready', onReady);
      source.removeEventListener('state_snapshot', onSnapshot);
      source.removeEventListener('open', onOpen);
      source.removeEventListener('error', onError);
      source.close();
    };
  }, [sessionId]);

  const participants = useMemo<ParticipantState[]>(() => {
    if (state === null) return [];
    const map = state.participants;
    // Stable, name-sorted order so the grid doesn't reshuffle when a
    // tick projects participants in a different insertion order.
    return Object.values(map).sort((a, b) => a.display_name.localeCompare(b.display_name));
  }, [state]);

  const statusLabel =
    connection === 'open' ? 'Live' : connection === 'connecting' ? 'Connecting…' : 'Disconnected';
  const statusDotClass =
    connection === 'open'
      ? 'bg-green-500'
      : connection === 'connecting'
        ? 'bg-yellow-500 animate-pulse'
        : 'bg-red-500';

  return (
    <section className="border-border-default bg-surface-primary flex flex-col gap-3 rounded-lg border p-6">
      <div className="flex items-center justify-between">
        <h2 className="text-text-primary text-lg font-medium">Participants</h2>
        <span className="text-text-secondary flex items-center gap-2 text-xs">
          <span className={`inline-block h-2 w-2 rounded-full ${statusDotClass}`} />
          {statusLabel}
        </span>
      </div>

      {state !== null && (
        <dl className="text-text-secondary grid grid-cols-3 gap-3 text-xs">
          <div className="flex flex-col">
            <dt className="text-text-tertiary uppercase tracking-wide">Speaking now</dt>
            <dd className="text-text-primary font-mono">{state.currently_speaking_count}</dd>
          </div>
          <div className="flex flex-col">
            <dt className="text-text-tertiary uppercase tracking-wide">Silence</dt>
            <dd className="text-text-primary font-mono">{formatSeconds(state.silence_run_sec)}</dd>
          </div>
          <div className="flex flex-col">
            <dt className="text-text-tertiary uppercase tracking-wide">Elapsed</dt>
            <dd className="text-text-primary font-mono">{formatSeconds(state.elapsed_sec)}</dd>
          </div>
        </dl>
      )}

      {participants.length === 0 ? (
        <p className="text-text-tertiary text-sm italic">
          {state === null
            ? 'Waiting for the first state snapshot…'
            : 'No participants in the room yet.'}
        </p>
      ) : (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {participants.map((p) => (
            <ParticipantTile key={p.participant_id} participant={p} />
          ))}
        </ul>
      )}
    </section>
  );
}

function ParticipantTile({ participant }: { participant: ParticipantState }): React.ReactElement {
  const flags = collectActiveFlags(participant);
  return (
    <li className="border-border-default bg-surface-secondary flex flex-col gap-2 rounded-md border p-3">
      <div className="flex items-center justify-between">
        <span className="text-text-primary font-medium">{participant.display_name}</span>
        <SpeakingIndicator
          isSpeaking={participant.is_currently_speaking}
          vadActive={participant.vad_active}
        />
      </div>
      <dl className="text-text-secondary grid grid-cols-2 gap-y-1 text-xs">
        <dt className="text-text-tertiary">Spoken</dt>
        <dd className="text-text-primary font-mono">
          {formatSeconds(participant.speaking_time_total_sec)}
        </dd>
        <dt className="text-text-tertiary">Turns</dt>
        <dd className="text-text-primary font-mono">{participant.turn_count}</dd>
        <dt className="text-text-tertiary">Share (5m)</dt>
        <dd className="text-text-primary font-mono">
          {participant.actual_share_last_5min_pct.toFixed(0)}% / fair{' '}
          {participant.fair_share_pct.toFixed(0)}%
        </dd>
        <dt className="text-text-tertiary">Last spoke</dt>
        <dd className="text-text-primary font-mono">
          {formatRelativeTime(participant.last_spoke_at)}
        </dd>
      </dl>
      {flags.length > 0 && (
        <ul className="flex flex-wrap gap-1">
          {flags.map((flag) => (
            <li
              key={flag}
              className="border-border-default text-text-secondary rounded-sm border bg-amber-100/30 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide dark:bg-amber-900/30"
            >
              {flag.replace(/_/g, ' ')}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function SpeakingIndicator({
  isSpeaking,
  vadActive,
}: {
  isSpeaking: boolean;
  vadActive: boolean;
}): React.ReactElement {
  if (isSpeaking) {
    return (
      <span className="flex items-center gap-1 text-xs font-medium text-green-700 dark:text-green-400">
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-green-500" />
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

function collectActiveFlags(participant: ParticipantState): string[] {
  const f = participant.flags;
  const active: string[] = [];
  if (f.dominating) active.push('dominating');
  if (f.silent_too_long) active.push('silent_too_long');
  if (f.frequently_interrupted) active.push('frequently_interrupted');
  if (f.disengaged) active.push('disengaged');
  return active;
}

function formatSeconds(value: number): string {
  if (!Number.isFinite(value)) return '—';
  if (value < 60) return `${value.toFixed(1)}s`;
  const mins = Math.floor(value / 60);
  const secs = Math.round(value - mins * 60);
  return `${mins}m ${secs.toString().padStart(2, '0')}s`;
}

function formatRelativeTime(iso: string | null): string {
  if (iso === null) return 'never';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const diff = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (diff < 5) return 'just now';
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function safeJsonParse(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}
