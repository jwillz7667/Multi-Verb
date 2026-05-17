/**
 * Smoke tests for the replay shell.
 *
 * Pins the L4-visible affordances so subsequent layers don't drift:
 *   - the "Replay mode" pin is rendered (brief §11.2: visually distinct
 *     from live),
 *   - the back link points to the live view,
 *   - the placeholder slots for timeline / audio / state / decision /
 *     filters / exports are present (L5–L13 will replace each),
 *   - the audio summary degrades gracefully across recording-state
 *     combinations (composite + per-participant, per-participant only,
 *     neither),
 *   - the decision detail pane shows the empty-selection copy when
 *     no marker has been clicked.
 *
 * The shell is a presentational client component; subsequent layers
 * will introduce real interactions and richer tests.
 */

import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ReplayShell } from './replay-shell';

import type { ReplayBootstrap } from './replay-types';

function makeBootstrap(overrides: Partial<ReplayBootstrap> = {}): ReplayBootstrap {
  return {
    session: {
      id: 'sess-1',
      livekit_room_name: 'room-abc',
      status: 'ended',
      scheduled_start: null,
      actual_start: '2026-05-01T10:00:00.000Z',
      actual_end: '2026-05-01T10:34:30.000Z',
      created_at: '2026-05-01T09:55:00.000Z',
      has_composite_recording: true,
      participant_recording_identities: ['alice-001', 'bob-002'],
    },
    participants: [
      {
        id: 'p-1',
        display_name: 'Alice',
        livekit_identity: 'alice-001',
        role: 'participant',
        joined_at: '2026-05-01T10:00:05.000Z',
        left_at: '2026-05-01T10:34:00.000Z',
      },
      {
        id: 'p-2',
        display_name: 'Bob',
        livekit_identity: 'bob-002',
        role: 'participant',
        joined_at: '2026-05-01T10:00:08.000Z',
        left_at: '2026-05-01T10:33:58.000Z',
      },
    ],
    decisions: [],
    flags: [],
    initial_utterances: [],
    initial_snapshots: [],
    ...overrides,
  };
}

describe('<ReplayShell />', () => {
  beforeEach(() => {
    // The shell mounts <ReplayAudioPlayer /> and <ReplayStateSnapshot />,
    // both of which dispatch a fetch on mount (signed-URL / snapshot-at).
    // These smoke tests assert on layout only — a never-resolving fetch
    // keeps the loading state stable and avoids React act() warnings
    // from late state updates after the synchronous render returns.
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => undefined)),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the "Replay mode" pin (visually distinct from live)', () => {
    render(<ReplayShell bootstrap={makeBootstrap()} />);

    expect(screen.getByTestId('replay-mode-pill')).toHaveTextContent(/Replay mode/i);
  });

  it('links back to the live session view', () => {
    render(<ReplayShell bootstrap={makeBootstrap()} />);

    const liveLink = screen.getByRole('link', { name: /live view/i });
    expect(liveLink).toHaveAttribute('href', '/sessions/sess-1');
  });

  it('reserves every L5–L13 layout slot so later layers drop in without re-flow', () => {
    render(<ReplayShell bootstrap={makeBootstrap()} />);

    expect(screen.getByTestId('replay-timeline-slot')).toBeInTheDocument();
    expect(screen.getByTestId('replay-audio-slot')).toBeInTheDocument();
    expect(screen.getByTestId('replay-filters-slot')).toBeInTheDocument();
    expect(screen.getByTestId('replay-state-slot')).toBeInTheDocument();
    expect(screen.getByTestId('replay-decision-slot')).toBeInTheDocument();
    expect(screen.getByTestId('replay-exports-slot')).toBeInTheDocument();
  });

  it('summarises composite + per-participant tracks when both are present', () => {
    render(<ReplayShell bootstrap={makeBootstrap()} />);

    expect(screen.getByTestId('replay-audio-slot')).toHaveTextContent(
      /composite \+ 2 per-participant tracks/i,
    );
  });

  it('degrades audio summary when only per-participant tracks exist', () => {
    render(
      <ReplayShell
        bootstrap={makeBootstrap({
          session: {
            ...makeBootstrap().session,
            has_composite_recording: false,
            participant_recording_identities: ['alice-001'],
          },
        })}
      />,
    );

    expect(screen.getByTestId('replay-audio-slot')).toHaveTextContent(
      /1 per-participant track \(no composite yet\)/i,
    );
  });

  it('says "no recording yet" when egress has not produced any keys', () => {
    render(
      <ReplayShell
        bootstrap={makeBootstrap({
          session: {
            ...makeBootstrap().session,
            has_composite_recording: false,
            participant_recording_identities: [],
          },
        })}
      />,
    );

    expect(screen.getByTestId('replay-audio-slot')).toHaveTextContent(/no recording yet/i);
  });

  it('shows the "select a marker" empty state in the decision pane', () => {
    render(<ReplayShell bootstrap={makeBootstrap()} />);

    expect(screen.getByTestId('replay-decision-slot')).toHaveTextContent(
      /Select a marker on the timeline to inspect a decision/i,
    );
  });

  it('renders a duration label computed from actual_start + actual_end', () => {
    render(<ReplayShell bootstrap={makeBootstrap()} />);

    // 10:00:00 → 10:34:30 = 34m 30s.
    expect(screen.getByText('34m 30s')).toBeInTheDocument();
  });

  it('falls back to "not started" when actual_start is null', () => {
    render(
      <ReplayShell
        bootstrap={makeBootstrap({
          session: {
            ...makeBootstrap().session,
            actual_start: null,
            actual_end: null,
          },
        })}
      />,
    );

    expect(screen.getByText(/not started/i)).toBeInTheDocument();
  });

  it('summarises decision counts (total + spoken) accurately', () => {
    render(
      <ReplayShell
        bootstrap={makeBootstrap({
          decisions: [
            makeDecision('d-1', { was_executed: false }),
            makeDecision('d-2', { was_executed: true }),
            makeDecision('d-3', { was_executed: true }),
          ],
        })}
      />,
    );

    expect(screen.getByText(/3 total · 2 spoken/i)).toBeInTheDocument();
  });

  it('renders filter chips for the actions present in the bootstrap decisions', () => {
    render(
      <ReplayShell
        bootstrap={makeBootstrap({
          decisions: [
            makeDecision('d-1', { action: 'stay_silent' }),
            makeDecision('d-2', { action: 'prompt' }),
          ],
        })}
      />,
    );

    expect(screen.getByTestId('replay-filter-actions-chip-stay_silent')).toBeInTheDocument();
    expect(screen.getByTestId('replay-filter-actions-chip-prompt')).toBeInTheDocument();
  });
});

function makeDecision(
  id: string,
  overrides: Partial<ReplayBootstrap['decisions'][number]> = {},
): ReplayBootstrap['decisions'][number] {
  return {
    id,
    tick_id: 0,
    ts: '2026-05-01T10:00:00.000Z',
    action: 'stay_silent',
    target_participant_id: null,
    source: 'rules_engine',
    triggering_rule: null,
    researcher_id: null,
    researcher_hint: null,
    reason_codes: [],
    reason_human: '',
    confidence: null,
    suppressed_by: [],
    was_executed: false,
    llm_prompt: null,
    llm_output: null,
    tts_audio_url: null,
    spoken_at: null,
    cooldown_until: '2026-05-01T10:00:00.000Z',
    ...overrides,
  };
}
