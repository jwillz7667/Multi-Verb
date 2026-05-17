/**
 * InterventionModals — spoken-intervention surface (P5 L9).
 *
 * Tests for the four modal-driven commands the researcher can issue:
 * `force_prompt`, `force_redirect`, `force_summary`, `whisper`. The
 * route-layer schema is exercised under
 * `api/sessions/[id]/commands/route.test.ts`; here we verify the
 * component constructs the envelope the engine actually expects, plus
 * the dialog UX (open/close, focus, error surfacing).
 *
 * SSE: `force_prompt` and `whisper` lazy-open an `EventSource` to learn
 * the participant roster. We stub a tiny `FakeEventSource` and emit a
 * single `state_snapshot` envelope to populate the `<select>`.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { InterventionModals } from './intervention-modals';

// ---------------------------------------------------------------------------
// Test doubles — fetch + EventSource
// ---------------------------------------------------------------------------

interface CapturedRequest {
  url: string;
  init: RequestInit;
}

function captureFetchMock(
  response: { ok: boolean; status?: number; body?: unknown } = {
    ok: true,
    body: {
      ok: true,
      command_id: '11111111-1111-4111-8111-111111111111',
      issued_at: '2026-05-17T12:00:00.000Z',
      stream_entry_id: '1700000000000-0',
    },
  },
): {
  fetchMock: ReturnType<typeof vi.fn>;
  requests: CapturedRequest[];
} {
  const requests: CapturedRequest[] = [];
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    requests.push({ url, init: init ?? {} });
    return Promise.resolve(
      new Response(JSON.stringify(response.body ?? {}), {
        status: response.status ?? (response.ok ? 200 : 500),
        headers: { 'content-type': 'application/json' },
      }),
    );
  });
  vi.stubGlobal('fetch', fetchMock);
  return { fetchMock, requests };
}

function parseBody(req: CapturedRequest): { command_type: string; payload: unknown } {
  return JSON.parse(req.init.body as string) as { command_type: string; payload: unknown };
}

// A minimal EventSource that exposes `.emit()` so tests can push
// snapshot envelopes synchronously. addEventListener / removeEventListener
// + close are the only surface the component touches.
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  readyState = 0;
  listeners: Record<string, ((e: MessageEvent<string>) => void)[]> = {};
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(name: string, fn: (e: MessageEvent<string>) => void): void {
    (this.listeners[name] ??= []).push(fn);
  }
  removeEventListener(name: string, fn: (e: MessageEvent<string>) => void): void {
    this.listeners[name] = (this.listeners[name] ?? []).filter((f) => f !== fn);
  }
  close(): void {
    this.readyState = 2;
  }
  emit(name: string, data: string): void {
    (this.listeners[name] ?? []).forEach((fn) => {
      fn(new MessageEvent(name, { data }));
    });
  }
}

function installFakeEventSource(): typeof FakeEventSource {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  return FakeEventSource;
}

// Build a syntactically-valid state_snapshot envelope for the SSE
// subscription. The component only reads `state.participants`, but the
// envelope must round-trip through `parseTranscriptEvent` (Zod), so we
// satisfy every required field with cheap defaults.
function buildSnapshotEvent(
  sessionId: string,
  participants: { id: string; displayName: string }[],
): string {
  const now = '2026-05-17T12:00:00.000Z';
  return JSON.stringify({
    type: 'state_snapshot',
    id: 'evt-1',
    session_id: sessionId,
    ts: now,
    payload: {
      snapshot_id: '22222222-2222-4222-8222-222222222222',
      state: {
        session_id: sessionId,
        tick_id: 1,
        t: now,
        started_at: now,
        scheduled_end_at: null,
        elapsed_sec: 0,
        participants: Object.fromEntries(
          participants.map((p) => [
            p.id,
            {
              participant_id: p.id,
              display_name: p.displayName,
              joined_at: now,
              speaking_time_total_sec: 0,
              speaking_time_last_5min_sec: 0,
              speaking_time_last_60sec: 0,
              turn_count: 0,
              last_spoke_at: null,
              last_spoke_duration_sec: null,
              is_currently_speaking: false,
              vad_active: false,
              backchannel_count_last_2min: 0,
              interruption_count: 0,
              was_interrupted_count: 0,
              recent_utterances: [],
              rolling_transcript_2min: '',
              flags: {
                dominating: false,
                silent_too_long: false,
                frequently_interrupted: false,
                disengaged: false,
              },
              fair_share_pct: 50,
              actual_share_last_5min_pct: 0,
            },
          ]),
        ),
        currently_speaking_count: 0,
        silence_run_sec: 0,
        rolling_global_transcript_2min: '',
        is_paused: false,
        moderator_muted: false,
        quietness_budget: {
          current_window_count: 0,
          last_utterance_at: null,
          max_utterances_per_10min: 3,
          min_seconds_between_utterances: 30,
        },
      },
    },
  });
}

const PARTICIPANT_A_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PARTICIPANT_B_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

beforeEach(() => {
  installFakeEventSource();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Shell + disabled states
// ---------------------------------------------------------------------------

describe('<InterventionModals /> — shell + disabled state', () => {
  it('renders four intervention buttons under the section heading', () => {
    captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="live" />);

    expect(screen.getByRole('heading', { name: /Interventions/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Prompt participant/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Redirect topic/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Summarise thread/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Whisper verbatim/ })).toBeInTheDocument();
  });

  it('disables every button and explains why when status is scheduled', () => {
    captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="scheduled" />);

    expect(screen.getByRole('button', { name: /Prompt participant/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Redirect topic/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Summarise thread/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Whisper verbatim/ })).toBeDisabled();
    expect(screen.getByText(/activate once the moderator joins/)).toBeInTheDocument();
  });

  it('disables every button and explains why when status is ended', () => {
    captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="ended" />);

    expect(screen.getByRole('button', { name: /Prompt participant/ })).toBeDisabled();
    expect(screen.getByText(/no longer accepted/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Force prompt
// ---------------------------------------------------------------------------

describe('<InterventionModals /> — force_prompt', () => {
  it('opens a dialog with target select + hint textarea when "Prompt participant" is clicked', async () => {
    captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Prompt participant/ }));

    const dialog = await screen.findByRole('dialog', { name: /Prompt a participant/ });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByLabelText(/Target participant/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Hint$/)).toBeInTheDocument();
  });

  it('keeps the submit button disabled while the hint is empty', async () => {
    captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Prompt participant/ }));
    await screen.findByRole('dialog');

    expect(screen.getByRole('button', { name: /Send to moderator/ })).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/^Hint$/), {
      target: { value: 'ask about the trust point' },
    });
    expect(screen.getByRole('button', { name: /Send to moderator/ })).toBeEnabled();
  });

  it('posts a force_prompt envelope with a null target when no participant is picked', async () => {
    const { requests } = captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Prompt participant/ }));
    await screen.findByRole('dialog');

    fireEvent.change(screen.getByLabelText(/^Hint$/), {
      target: { value: '  ask about the trust point  ' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Send to moderator/ }));

    await waitFor(() => {
      expect(requests).toHaveLength(1);
    });
    expect(requests[0]?.url).toBe('/api/sessions/s-1/commands');
    expect(parseBody(requests[0]!)).toEqual({
      command_type: 'force_prompt',
      payload: {
        prompt: 'ask about the trust point', // trim()
        target_participant_id: null,
      },
    });
  });

  it('threads the picked target_participant_id through and surfaces participants from SSE', async () => {
    const { requests } = captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Prompt participant/ }));
    await screen.findByRole('dialog');

    // Modal mount triggers EventSource. Emit a snapshot so the
    // participant <select> renders Maya + Priya.
    expect(FakeEventSource.instances).toHaveLength(1);
    const source = FakeEventSource.instances[0]!;
    act(() => {
      source.emit(
        'state_snapshot',
        buildSnapshotEvent('11111111-1111-4111-8111-111111111111', [
          { id: PARTICIPANT_A_ID, displayName: 'Maya' },
          { id: PARTICIPANT_B_ID, displayName: 'Priya' },
        ]),
      );
    });

    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Maya' })).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/Target participant/), {
      target: { value: PARTICIPANT_B_ID },
    });
    fireEvent.change(screen.getByLabelText(/^Hint$/), {
      target: { value: 'follow up on the voice flattening point' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Send to moderator/ }));

    await waitFor(() => {
      expect(requests).toHaveLength(1);
    });
    expect(parseBody(requests[0]!)).toEqual({
      command_type: 'force_prompt',
      payload: {
        prompt: 'follow up on the voice flattening point',
        target_participant_id: PARTICIPANT_B_ID,
      },
    });
  });

  it('closes the dialog after a successful submit', async () => {
    captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Prompt participant/ }));
    await screen.findByRole('dialog');

    fireEvent.change(screen.getByLabelText(/^Hint$/), { target: { value: 'hi' } });
    fireEvent.click(screen.getByRole('button', { name: /Send to moderator/ }));

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// Force redirect
// ---------------------------------------------------------------------------

describe('<InterventionModals /> — force_redirect', () => {
  it('posts a force_redirect envelope with the topic', async () => {
    const { requests } = captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Redirect topic/ }));
    await screen.findByRole('dialog', { name: /Redirect topic/ });

    fireEvent.change(screen.getByLabelText(/^Topic$/), {
      target: { value: 'bring us back to the trust question' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Send to moderator/ }));

    await waitFor(() => {
      expect(requests).toHaveLength(1);
    });
    expect(parseBody(requests[0]!)).toEqual({
      command_type: 'force_redirect',
      payload: { topic: 'bring us back to the trust question' },
    });
  });
});

// ---------------------------------------------------------------------------
// Force summary
// ---------------------------------------------------------------------------

describe('<InterventionModals /> — force_summary', () => {
  it('posts an empty payload when no focus is supplied', async () => {
    const { requests } = captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Summarise thread/ }));
    await screen.findByRole('dialog', { name: /Summarise thread/ });

    fireEvent.click(screen.getByRole('button', { name: /Send to moderator/ }));

    await waitFor(() => {
      expect(requests).toHaveLength(1);
    });
    expect(parseBody(requests[0]!)).toEqual({
      command_type: 'force_summary',
      payload: {},
    });
  });

  it('threads the focus through when supplied', async () => {
    const { requests } = captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Summarise thread/ }));
    await screen.findByRole('dialog');

    fireEvent.change(screen.getByLabelText(/Focus/), {
      target: { value: "what we've heard about trust so far" },
    });
    fireEvent.click(screen.getByRole('button', { name: /Send to moderator/ }));

    await waitFor(() => {
      expect(requests).toHaveLength(1);
    });
    expect(parseBody(requests[0]!)).toEqual({
      command_type: 'force_summary',
      payload: { focus: "what we've heard about trust so far" },
    });
  });
});

// ---------------------------------------------------------------------------
// Whisper
// ---------------------------------------------------------------------------

describe('<InterventionModals /> — whisper', () => {
  it('posts a whisper envelope with verbatim text and null target by default', async () => {
    const { requests } = captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Whisper verbatim/ }));
    await screen.findByRole('dialog', { name: /Whisper verbatim/ });

    fireEvent.change(screen.getByLabelText(/Spoken text/), {
      target: { value: 'And what about you, Sam?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Speak verbatim/ }));

    await waitFor(() => {
      expect(requests).toHaveLength(1);
    });
    expect(parseBody(requests[0]!)).toEqual({
      command_type: 'whisper',
      payload: {
        text: 'And what about you, Sam?',
        target_participant_id: null,
      },
    });
  });

  it('threads the picked target through', async () => {
    const { requests } = captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Whisper verbatim/ }));
    await screen.findByRole('dialog', { name: /Whisper verbatim/ });

    const source = FakeEventSource.instances[0]!;
    act(() => {
      source.emit(
        'state_snapshot',
        buildSnapshotEvent('11111111-1111-4111-8111-111111111111', [
          { id: PARTICIPANT_A_ID, displayName: 'Sam' },
        ]),
      );
    });

    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Sam' })).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/Target participant/), {
      target: { value: PARTICIPANT_A_ID },
    });
    fireEvent.change(screen.getByLabelText(/Spoken text/), {
      target: { value: 'Sam, what about you?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Speak verbatim/ }));

    await waitFor(() => {
      expect(requests).toHaveLength(1);
    });
    expect(parseBody(requests[0]!)).toEqual({
      command_type: 'whisper',
      payload: { text: 'Sam, what about you?', target_participant_id: PARTICIPANT_A_ID },
    });
  });
});

// ---------------------------------------------------------------------------
// Dialog UX
// ---------------------------------------------------------------------------

describe('<InterventionModals /> — dialog UX', () => {
  it('closes on Escape', async () => {
    captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Redirect topic/ }));
    await screen.findByRole('dialog');

    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('closes on Cancel click', async () => {
    captureFetchMock();
    render(<InterventionModals sessionId="s-1" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Summarise thread/ }));
    await screen.findByRole('dialog');

    fireEvent.click(screen.getByRole('button', { name: /Cancel/ }));

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('surfaces a route error as an alert and keeps the dialog open', async () => {
    captureFetchMock({ ok: false, status: 409, body: { error: 'session_already_ended' } });
    render(<InterventionModals sessionId="s-1" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Redirect topic/ }));
    await screen.findByRole('dialog');

    fireEvent.change(screen.getByLabelText(/^Topic$/), { target: { value: 'whatever' } });
    fireEvent.click(screen.getByRole('button', { name: /Send to moderator/ }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('session_already_ended');
    // Dialog remains open so the researcher can retry or cancel.
    expect(screen.queryByRole('dialog')).toBeInTheDocument();
  });
});
