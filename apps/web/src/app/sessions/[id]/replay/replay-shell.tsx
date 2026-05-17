'use client';

/**
 * Replay shell — visual frame + state coordination for the replay UI.
 *
 * Phase 6 L4 ships the skeleton: layout slots, scrubber state, the
 * selected-decision state, and placeholder panes for the timeline,
 * audio player, state snapshot panel, decision panel, filters, and
 * exports. Subsequent layers fill the panes:
 *
 *   - L5: timeline component (speech bands, decision markers, flags)
 *   - L6: audio player + scrubber sync
 *   - L7: state snapshot panel
 *   - L8: decision detail panel + rule evaluations
 *   - L9: filter bar (action / participant / rule / source)
 *   - L10-L13: export panel buttons
 *
 * The shell holds the cross-cutting state that those layers will read:
 *
 *   - `currentTs`: the scrubber position (ISO string, mirrors brief's
 *     `(ts, id)` convention so it composes with the wire shapes).
 *   - `selectedDecisionId`: which decision is open in the detail pane.
 *   - `filters`: action / participant / rule / source.
 *
 * Visually distinct from the live page per brief §11.2: a darker
 * `bg-surface-secondary` chrome and no red live indicator. The header
 * pin clarifies "Replay mode" so a researcher who lands here from a
 * deep link never confuses it with a live session.
 */

import Link from 'next/link';
import { useCallback, useMemo, useState } from 'react';

import { ReplayTimeline } from './replay-timeline';

import type { ReplayBootstrap } from './replay-types';

interface Props {
  bootstrap: ReplayBootstrap;
}

export function ReplayShell({ bootstrap }: Props): React.ReactElement {
  const { session, participants, decisions, flags, initial_utterances } = bootstrap;

  // The initial scrubber position is `actual_start` if present —
  // otherwise we fall back to `created_at` so the page still renders
  // when a session row was never moved to "live" (a no-op replay, but
  // the UI doesn't crash).
  const initialTs = session.actual_start ?? session.created_at;
  const [currentTs, setCurrentTs] = useState<string>(initialTs);
  const [selectedDecisionId, setSelectedDecisionId] = useState<string | null>(null);

  const handleSeek = useCallback((ts: string): void => {
    setCurrentTs(ts);
  }, []);
  const handleSelectDecision = useCallback((decisionId: string): void => {
    setSelectedDecisionId(decisionId);
  }, []);

  const durationLabel = useMemo(
    () => formatDurationLabel(session.actual_start, session.actual_end),
    [session.actual_start, session.actual_end],
  );

  const executedDecisionCount = useMemo(
    () => decisions.filter((d) => d.was_executed).length,
    [decisions],
  );

  return (
    <main className="min-h-screen bg-surface-secondary text-text-primary">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-10">
        <div className="flex items-center justify-between">
          <Link
            href={`/sessions/${session.id}`}
            className="text-text-secondary text-sm hover:underline"
          >
            ← Live view
          </Link>
          <span
            className="border-accent-border bg-accent-bg text-accent rounded-full border px-3 py-0.5 text-xs font-medium uppercase tracking-wide"
            data-testid="replay-mode-pill"
          >
            Replay mode
          </span>
        </div>

        <header className="flex flex-col gap-2">
          <h1 className="text-2xl font-medium tracking-tight">
            {session.livekit_room_name}
            <span className="text-text-tertiary ml-3 font-mono text-sm">{session.id}</span>
          </h1>
          <dl className="text-text-secondary grid grid-cols-2 gap-x-6 gap-y-1 text-sm md:grid-cols-4">
            <dt>Status</dt>
            <dd className="font-mono">{session.status}</dd>
            <dt>Duration</dt>
            <dd>{durationLabel}</dd>
            <dt>Participants</dt>
            <dd>{participants.length}</dd>
            <dt>Decisions</dt>
            <dd>
              {decisions.length} total · {executedDecisionCount} spoken
            </dd>
          </dl>
        </header>

        {/*
          L5 — timeline. Three swimlanes (speech bands, decisions,
          flags) over a shared time axis. Click anywhere on the axis
          seeks the scrubber; click a decision marker also opens it in
          the detail pane.
        */}
        <section
          className="border-border-default bg-surface-primary flex flex-col gap-2 rounded-lg border px-6 py-4"
          data-testid="replay-timeline-slot"
        >
          <div className="flex items-center justify-between">
            <p className="text-text-secondary text-xs">
              {participants.length} participant lane{participants.length === 1 ? '' : 's'} ·{' '}
              {decisions.length} decision{decisions.length === 1 ? '' : 's'} · {flags.length} flag
              {flags.length === 1 ? '' : 's'}
            </p>
            <p className="text-text-tertiary font-mono text-xs">@ {currentTs}</p>
          </div>
          <ReplayTimeline
            session={session}
            participants={participants}
            utterances={initial_utterances}
            decisions={decisions}
            flags={flags}
            currentTs={currentTs}
            selectedDecisionId={selectedDecisionId}
            onSeek={handleSeek}
            onSelectDecision={handleSelectDecision}
          />
        </section>

        {/*
          L6 — audio player + scrubber sync. Composite playback uses
          GET /api/sessions/[id]/recordings/audio (no participant arg);
          per-participant tracks add ?participant=<identity>. The
          placeholder makes the affordance visible to QA without yet
          mounting an <audio> element.
        */}
        <section
          className="border-border-default bg-surface-primary flex items-center justify-between rounded-lg border px-6 py-4"
          data-testid="replay-audio-slot"
        >
          <div className="flex flex-col">
            <span className="text-text-primary text-sm font-medium">Audio</span>
            <span className="text-text-tertiary text-xs">
              {session.has_composite_recording
                ? `composite + ${session.participant_recording_identities.length} per-participant track${session.participant_recording_identities.length === 1 ? '' : 's'}`
                : session.participant_recording_identities.length > 0
                  ? `${session.participant_recording_identities.length} per-participant track${session.participant_recording_identities.length === 1 ? '' : 's'} (no composite yet)`
                  : 'no recording yet — egress may still be running'}
            </span>
          </div>
          <button
            type="button"
            className="border-border-default text-text-secondary hover:bg-surface-tertiary cursor-not-allowed rounded-md border px-3 py-1.5 text-xs"
            disabled
            data-testid="replay-audio-play"
          >
            ▶︎ Play · P6 L6
          </button>
        </section>

        {/*
          L9 — filters. Action / participant / rule / source. Wired in
          L9; the placeholder reserves the chrome so the page doesn't
          re-flow on first paint of that layer.
        */}
        <section
          className="border-border-default bg-surface-primary flex flex-wrap items-center gap-3 rounded-lg border px-6 py-3"
          data-testid="replay-filters-slot"
        >
          <span className="text-text-tertiary text-xs uppercase tracking-wide">Filters</span>
          <span className="text-text-secondary text-xs">action · participant · rule · source</span>
          <span className="text-text-tertiary ml-auto text-xs">interactive in P6 L9</span>
        </section>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/*
            L7 — state snapshot panel. Shows the SessionState frozen at
            the scrubber position. Selecting a decision in the right
            pane drives the scrubber, which re-renders this pane.
          */}
          <section
            className="border-border-default bg-surface-primary flex min-h-[20rem] flex-col gap-2 rounded-lg border p-6"
            data-testid="replay-state-slot"
          >
            <h2 className="text-text-primary text-sm font-medium uppercase tracking-wide">
              State snapshot
            </h2>
            <p className="text-text-tertiary font-mono text-xs">@ {currentTs}</p>
            <p className="text-text-secondary mt-4 text-sm">
              {bootstrap.initial_snapshots.length} initial snapshot
              {bootstrap.initial_snapshots.length === 1 ? '' : 's'} loaded; pane wiring ships in P6
              L7.
            </p>
          </section>

          {/*
            L8 — decision detail panel. Renders the selected decision's
            reason codes, every rule evaluation (firing + suppressed
            with reason), and the LLM prompt + output. Click handlers
            on the timeline markers and the audio scrubber update
            `selectedDecisionId`.
          */}
          <section
            className="border-border-default bg-surface-primary flex min-h-[20rem] flex-col gap-2 rounded-lg border p-6"
            data-testid="replay-decision-slot"
          >
            <h2 className="text-text-primary text-sm font-medium uppercase tracking-wide">
              Decision detail
            </h2>
            {selectedDecisionId === null ? (
              <p className="text-text-secondary mt-2 text-sm">
                Select a marker on the timeline to inspect a decision.
              </p>
            ) : (
              <p className="text-text-tertiary font-mono text-xs">selected: {selectedDecisionId}</p>
            )}
            <p className="text-text-tertiary mt-4 text-xs">
              Full rule-evaluation breakdown + LLM prompt/output ship in P6 L8.
            </p>
          </section>
        </div>

        {/*
          L10–L13 — exports. Disabled placeholders for transcript,
          decision log, snapshots, audio clips. The layout reserves a
          row so the page is the same height when the export buttons
          land — no flicker when the researcher hits the page.
        */}
        <section
          className="border-border-default bg-surface-primary flex flex-wrap items-center gap-3 rounded-lg border px-6 py-4"
          data-testid="replay-exports-slot"
        >
          <span className="text-text-primary text-sm font-medium">Export</span>
          {(['Transcript', 'Decision log', 'State snapshots', 'Flagged clips'] as const).map(
            (label) => (
              <button
                key={label}
                type="button"
                className="border-border-default text-text-tertiary cursor-not-allowed rounded-md border px-3 py-1 text-xs"
                disabled
              >
                {label}
              </button>
            ),
          )}
          <span className="text-text-tertiary ml-auto text-xs">P6 L10–L13</span>
        </section>

        <button
          type="button"
          className="text-text-tertiary self-end text-xs underline"
          onClick={(): void => {
            setCurrentTs(initialTs);
          }}
        >
          Reset scrubber
        </button>
      </div>
    </main>
  );
}

function formatDurationLabel(actualStart: string | null, actualEnd: string | null): string {
  if (actualStart === null) return '— not started —';
  if (actualEnd === null) return 'still live';
  const start = Date.parse(actualStart);
  const end = Date.parse(actualEnd);
  if (Number.isNaN(start) || Number.isNaN(end) || end <= start) return '—';
  const totalSec = Math.round((end - start) / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min.toString()}m ${sec.toString().padStart(2, '0')}s`;
}
