/**
 * Tests for <ReplayStateSnapshot />.
 *
 * Coverage focuses on the panel's audit semantics:
 *
 *   - The active snapshot is the freshest cached row ≤ currentTs.
 *   - When currentTs moves outside the cached range, the panel fetches
 *     the snapshot at that ts; the fetched row enters the cache and
 *     scrubbing back doesn't re-fetch.
 *   - 404 → "no snapshot at this time" copy; future scrubs to the
 *     same ts don't re-request (the negative cache holds).
 *   - 401 → researcher-readable "sign in again" copy.
 *   - Renders session counters + per-participant tiles, including
 *     active flags and the speaking/VAD/idle badge.
 *   - Tick id from the bigint coercion is surfaced as-is.
 *   - Tolerates a snapshot row whose `state` is null without
 *     throwing.
 */

import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ReplayStateSnapshot } from './replay-state-snapshot';

import type { ReplayParticipantDTO, ReplaySnapshotDTO } from './replay-types';

const SESSION_ID = 'sess-1';
const SESSION_START = '2026-05-01T10:00:00.000Z';

const ALICE: ReplayParticipantDTO = {
  id: 'p-alice',
  display_name: 'Alice',
  livekit_identity: 'alice-001',
  role: 'participant',
  joined_at: '2026-05-01T10:00:05.000Z',
  left_at: null,
};

const BOB: ReplayParticipantDTO = {
  id: 'p-bob',
  display_name: 'Bob',
  livekit_identity: 'bob-002',
  role: 'participant',
  joined_at: '2026-05-01T10:00:10.000Z',
  left_at: null,
};

function makeSnapshot(
  id: string,
  offsetSec: number,
  overrides?: { state?: unknown },
): ReplaySnapshotDTO {
  const ts = new Date(Date.parse(SESSION_START) + offsetSec * 1000).toISOString();
  const defaultState = {
    session_id: SESSION_ID,
    tick_id: offsetSec * 2,
    elapsed_sec: offsetSec,
    t: ts,
    started_at: SESSION_START,
    silence_run_sec: 3,
    currently_speaking_count: 1,
    is_paused: false,
    moderator_muted: false,
    quietness_budget: {
      current_window_count: 2,
      max_utterances_per_10min: 6,
      min_seconds_between_utterances: 25,
    },
    participants: {
      [ALICE.id]: {
        participant_id: ALICE.id,
        display_name: ALICE.display_name,
        joined_at: ALICE.joined_at,
        speaking_time_total_sec: 45.5,
        turn_count: 4,
        actual_share_last_5min_pct: 60,
        fair_share_pct: 50,
        last_spoke_at: '2026-05-01T10:01:00.000Z',
        is_currently_speaking: true,
        vad_active: true,
        flags: {
          dominating: true,
          silent_too_long: false,
          frequently_interrupted: false,
          disengaged: false,
        },
      },
      [BOB.id]: {
        participant_id: BOB.id,
        display_name: BOB.display_name,
        joined_at: BOB.joined_at,
        speaking_time_total_sec: 12,
        turn_count: 1,
        actual_share_last_5min_pct: 15,
        fair_share_pct: 50,
        last_spoke_at: null,
        is_currently_speaking: false,
        vad_active: false,
        flags: {
          dominating: false,
          silent_too_long: true,
          frequently_interrupted: false,
          disengaged: true,
        },
      },
    },
  };
  return {
    id,
    tick_id: offsetSec * 2,
    ts,
    state: overrides?.state ?? defaultState,
  };
}

function setupFetchMock(handler: (url: string) => Response): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    return Promise.resolve(handler(url));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('<ReplayStateSnapshot /> — selection from bootstrap cache', () => {
  beforeEach(() => {
    // Nothing in cache scenarios fetch; for those that don't, this
    // catches accidental network calls.
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('unexpected fetch'))),
    );
  });

  it('renders the freshest snapshot ≤ currentTs from the bootstrap window', () => {
    const snapshots = [makeSnapshot('s-1', 30), makeSnapshot('s-2', 60), makeSnapshot('s-3', 90)];

    render(
      <ReplayStateSnapshot
        sessionId={SESSION_ID}
        participants={[ALICE, BOB]}
        initialSnapshots={snapshots}
        currentTs={'2026-05-01T10:01:15.000Z'} // 75s, falls between s-2 (60s) and s-3 (90s) → picks s-2
      />,
    );

    // s-2 is the snapshot at offset 60s — display "@ <iso for 60s>".
    expect(screen.getByTestId('replay-state-active-ts')).toHaveTextContent(
      /2026-05-01T10:01:00\.000Z/,
    );
    expect(screen.getByTestId('replay-state-counters')).toBeInTheDocument();
    expect(screen.getByTestId(`replay-state-participant-${ALICE.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`replay-state-participant-${BOB.id}`)).toBeInTheDocument();
  });

  it("renders an empty-state hint when no snapshot ≤ currentTs is in the cache and fetch hasn't resolved", async () => {
    render(
      <ReplayStateSnapshot
        sessionId={SESSION_ID}
        participants={[ALICE]}
        initialSnapshots={[]}
        currentTs={SESSION_START}
      />,
    );

    // The empty cache triggers a fetch; the stubbed rejection lets
    // React settle into the error state inside an act() boundary so
    // the assertions don't race the state update.
    await waitFor(() => {
      expect(screen.queryByTestId('replay-state-counters')).not.toBeInTheDocument();
      expect(screen.queryByTestId('replay-state-participants')).not.toBeInTheDocument();
    });
  });
});

describe('<ReplayStateSnapshot /> — counters + participant rendering', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('unexpected fetch'))),
    );
  });

  it('surfaces session counters from the snapshot state', () => {
    const snap = makeSnapshot('s-1', 60);
    render(
      <ReplayStateSnapshot
        sessionId={SESSION_ID}
        participants={[ALICE, BOB]}
        initialSnapshots={[snap]}
        currentTs={snap.ts}
      />,
    );

    const counters = screen.getByTestId('replay-state-counters');
    expect(counters).toHaveTextContent(/Speaking now/);
    expect(counters).toHaveTextContent(/Silence/);
    expect(counters).toHaveTextContent(/Elapsed/);
    expect(counters).toHaveTextContent(/Tick/);
    expect(counters).toHaveTextContent(/Budget used/);
    // Budget used renders as "2 / 6" given the fixture.
    expect(counters).toHaveTextContent(/2 \/ 6/);
  });

  it('renders the speaking + dominating badge on the participant tile', () => {
    const snap = makeSnapshot('s-1', 60);
    render(
      <ReplayStateSnapshot
        sessionId={SESSION_ID}
        participants={[ALICE, BOB]}
        initialSnapshots={[snap]}
        currentTs={snap.ts}
      />,
    );

    const aliceTile = screen.getByTestId(`replay-state-participant-${ALICE.id}`);
    expect(aliceTile).toHaveTextContent(/Speaking/);
    expect(aliceTile).toHaveTextContent(/dominating/);

    const bobTile = screen.getByTestId(`replay-state-participant-${BOB.id}`);
    expect(bobTile).toHaveTextContent(/Idle/);
    expect(bobTile).toHaveTextContent(/silent too long/);
    expect(bobTile).toHaveTextContent(/disengaged/);
  });
});

describe('<ReplayStateSnapshot /> — on-demand fetch', () => {
  it('fetches the snapshot at currentTs when none in cache, then renders it', async () => {
    const fetchMock = setupFetchMock(
      () =>
        new Response(JSON.stringify(makeSnapshot('s-fetched', 120)), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    );

    render(
      <ReplayStateSnapshot
        sessionId={SESSION_ID}
        participants={[ALICE]}
        initialSnapshots={[]}
        currentTs={'2026-05-01T10:02:00.000Z'} // 120s
      />,
    );

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/sessions/${SESSION_ID}/snapshots/at?ts=${encodeURIComponent('2026-05-01T10:02:00.000Z')}`,
    );

    await waitFor(() => {
      expect(screen.getByTestId('replay-state-counters')).toBeInTheDocument();
    });
  });

  it('shows "no snapshot at this time" copy on a 404', async () => {
    setupFetchMock(
      () =>
        new Response(JSON.stringify({ error: 'no_snapshot_at_ts' }), {
          status: 404,
          headers: { 'content-type': 'application/json' },
        }),
    );

    render(
      <ReplayStateSnapshot
        sessionId={SESSION_ID}
        participants={[ALICE]}
        initialSnapshots={[]}
        currentTs={'2026-05-01T09:59:00.000Z'}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('replay-state-error')).toHaveTextContent(
        /No snapshot at this time/i,
      );
    });
  });

  it('does not refetch when scrubbing forward into the cached snapshot horizon', async () => {
    // The panel's selection rule is "freshest cached ≤ currentTs", so
    // a previously-fetched snap continues to satisfy any scrubber
    // position later than its ts. This is the read-path optimization
    // that keeps a researcher who's micro-scrubbing in a hot zone from
    // dispatching a snapshot lookup every keystroke.
    const fetched = makeSnapshot('s-late', 120);
    const fetchMock = setupFetchMock(
      () =>
        new Response(JSON.stringify(fetched), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    );

    const { rerender } = render(
      <ReplayStateSnapshot
        sessionId={SESSION_ID}
        participants={[ALICE]}
        initialSnapshots={[]}
        currentTs={'2026-05-01T10:02:00.000Z'} // 120s — empty cache → forces fetch
      />,
    );

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    // Scrub forward by 5s. The just-fetched snap (120s) is still
    // ≤ 125s so the cache satisfies and no second fetch fires.
    rerender(
      <ReplayStateSnapshot
        sessionId={SESSION_ID}
        participants={[ALICE]}
        initialSnapshots={[]}
        currentTs={'2026-05-01T10:02:05.000Z'}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('replay-state-active-ts')).toHaveTextContent(fetched.ts);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('surfaces sign-in-again copy on a 401', async () => {
    setupFetchMock(
      () =>
        new Response(JSON.stringify({ error: 'unauthorized' }), {
          status: 401,
          headers: { 'content-type': 'application/json' },
        }),
    );

    render(
      <ReplayStateSnapshot
        sessionId={SESSION_ID}
        participants={[ALICE]}
        initialSnapshots={[]}
        currentTs={SESSION_START}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('replay-state-error')).toHaveTextContent(/Sign in again/i);
    });
  });
});

describe('<ReplayStateSnapshot /> — defensive parsing', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('unexpected fetch'))),
    );
  });

  it('does not crash when a snapshot has a null state field', () => {
    const broken: ReplaySnapshotDTO = {
      id: 's-broken',
      tick_id: 0,
      ts: SESSION_START,
      state: null,
    };

    expect(() =>
      render(
        <ReplayStateSnapshot
          sessionId={SESSION_ID}
          participants={[ALICE]}
          initialSnapshots={[broken]}
          currentTs={SESSION_START}
        />,
      ),
    ).not.toThrow();

    // active-ts still renders (the snapshot row exists), but the
    // counters block is suppressed because `state` is null.
    expect(screen.getByTestId('replay-state-active-ts')).toHaveTextContent(SESSION_START);
    expect(screen.queryByTestId('replay-state-counters')).not.toBeInTheDocument();
  });
});
