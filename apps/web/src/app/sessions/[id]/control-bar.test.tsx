/**
 * ControlBar — researcher live-control surface (P5 L8).
 *
 * Verifies the three things the shell promises:
 *   1. Each affordance POSTs the right `ResearcherCommand` envelope.
 *   2. Optimistic state holds on success and reverts on failure.
 *   3. The quietness dial debounces and translates 1–10 onto the
 *      engine's `(max_utterances_per_10min, min_seconds_between)` shape.
 *
 * Boundary tests at the route layer
 * (`api/sessions/[id]/commands/route.test.ts`) already cover schema
 * validation and 404/409 mapping; we don't redo that here.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ControlBar, quietnessPositionToBudget } from './control-bar';

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

function parseBody(req: CapturedRequest): { command_type: string; payload?: unknown } {
  return JSON.parse(req.init.body as string) as { command_type: string; payload?: unknown };
}

describe('quietnessPositionToBudget', () => {
  it('maps the dial midpoint to engine defaults (3 / 30s)', () => {
    expect(quietnessPositionToBudget(5)).toEqual({
      max_utterances_per_10min: 3,
      min_seconds_between_utterances: 30,
    });
  });

  it('is monotonic in both axes — higher position = more utterances + shorter floor', () => {
    let prevMax = 0;
    let prevMin = Number.POSITIVE_INFINITY;
    for (let i = 1; i <= 10; i++) {
      const budget = quietnessPositionToBudget(i);
      expect(budget.max_utterances_per_10min).toBeGreaterThanOrEqual(prevMax);
      expect(budget.min_seconds_between_utterances).toBeLessThanOrEqual(prevMin);
      prevMax = budget.max_utterances_per_10min;
      prevMin = budget.min_seconds_between_utterances;
    }
  });

  it('clamps positions outside [1, 10]', () => {
    expect(quietnessPositionToBudget(0)).toEqual(quietnessPositionToBudget(1));
    expect(quietnessPositionToBudget(-5)).toEqual(quietnessPositionToBudget(1));
    expect(quietnessPositionToBudget(50)).toEqual(quietnessPositionToBudget(10));
  });

  it('rounds fractional positions', () => {
    expect(quietnessPositionToBudget(5.4)).toEqual(quietnessPositionToBudget(5));
    expect(quietnessPositionToBudget(5.6)).toEqual(quietnessPositionToBudget(6));
  });
});

describe('<ControlBar /> — disabled state', () => {
  it('disables every affordance when the session is not yet live', () => {
    const { fetchMock } = captureFetchMock();
    render(<ControlBar sessionId="s-1" status="scheduled" />);

    expect(screen.getByRole('button', { name: /Mute moderator/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Pause session/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Flag moment/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /End session/ })).toBeDisabled();
    expect(screen.getByRole('slider', { name: /Quietness budget/ })).toBeDisabled();

    expect(
      screen.getByText(/Controls activate once the moderator joins the room/),
    ).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows the ended hint when the session has terminated', () => {
    captureFetchMock();
    render(<ControlBar sessionId="s-1" status="ended" />);
    expect(screen.getByText(/Session has ended/)).toBeInTheDocument();
  });
});

describe('<ControlBar /> — mute toggle', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('posts mute_moderator then unmute_moderator across two clicks', async () => {
    const { fetchMock, requests } = captureFetchMock();
    render(<ControlBar sessionId="s-mute" status="live" />);

    const btn = screen.getByRole('button', { name: /Mute moderator/ });
    fireEvent.click(btn);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    expect(parseBody(requests[0]!)).toEqual({ command_type: 'mute_moderator', payload: {} });
    expect(requests[0]!.url).toBe('/api/sessions/s-mute/commands');

    // Label flips optimistically to "Muted" and aria-pressed becomes true.
    expect(await screen.findByRole('button', { name: /Muted/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    fireEvent.click(screen.getByRole('button', { name: /Muted/ }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
    expect(parseBody(requests[1]!)).toEqual({ command_type: 'unmute_moderator', payload: {} });
    expect(await screen.findByRole('button', { name: /Mute moderator/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('reverts the optimistic toggle and surfaces an error when the POST fails', async () => {
    const { fetchMock } = captureFetchMock({
      ok: false,
      status: 409,
      body: { error: 'session_already_ended' },
    });
    render(<ControlBar sessionId="s-fail" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Mute moderator/ }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    // Optimistic label flips back to "Mute moderator" and the alert appears.
    const reverted = await screen.findByRole('button', { name: /Mute moderator/ });
    expect(reverted).toHaveAttribute('aria-pressed', 'false');
    expect(await screen.findByRole('alert')).toHaveTextContent('session_already_ended');
  });
});

describe('<ControlBar /> — pause toggle', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('posts pause_session and resume_session in sequence', async () => {
    const { fetchMock, requests } = captureFetchMock();
    render(<ControlBar sessionId="s-pause" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Pause session/ }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    expect(parseBody(requests[0]!)).toEqual({ command_type: 'pause_session', payload: {} });

    fireEvent.click(await screen.findByRole('button', { name: /Resume session/ }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
    expect(parseBody(requests[1]!)).toEqual({ command_type: 'resume_session', payload: {} });
  });
});

describe('<ControlBar /> — quietness dial', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('debounces a drag and only posts the final value', async () => {
    const { fetchMock, requests } = captureFetchMock();
    render(<ControlBar sessionId="s-q" status="live" />);

    const slider = screen.getByRole('slider', { name: /Quietness budget/ });
    // Rapid drag: 5 → 4 → 3 → 2 within the debounce window.
    fireEvent.change(slider, { target: { value: '4' } });
    fireEvent.change(slider, { target: { value: '3' } });
    fireEvent.change(slider, { target: { value: '2' } });

    expect(fetchMock).not.toHaveBeenCalled();

    // `advanceTimersByTimeAsync` drains the debounce timer *and* the
    // microtask chain inside `runCommand` (fetch → text → state flip),
    // so the busy-flag re-render lands before React's strict-mode act
    // checker scolds us about an un-wrapped update.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(parseBody(requests[0]!)).toEqual({
      command_type: 'set_quietness_budget',
      payload: quietnessPositionToBudget(2),
    });
  });

  it('does not re-POST when the user lands back on the same position they committed to', async () => {
    const { fetchMock } = captureFetchMock();
    render(<ControlBar sessionId="s-q" status="live" />);
    const slider = screen.getByRole('slider', { name: /Quietness budget/ });

    fireEvent.change(slider, { target: { value: '7' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Drag away and back to 7.
    fireEvent.change(slider, { target: { value: '4' } });
    fireEvent.change(slider, { target: { value: '7' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1); // dedup — same committed value
  });
});

describe('<ControlBar /> — flag moment', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('reveals the note field, posts flag_moment with the trimmed note, and resets', async () => {
    const { fetchMock, requests } = captureFetchMock();
    render(<ControlBar sessionId="s-flag" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Flag moment/ }));
    const textarea = await screen.findByLabelText(/Flag note/);
    fireEvent.change(textarea, { target: { value: '   interesting cross-talk   ' } });
    fireEvent.click(screen.getByRole('button', { name: /Save flag/ }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    expect(parseBody(requests[0]!)).toEqual({
      command_type: 'flag_moment',
      payload: { note: 'interesting cross-talk' },
    });

    // Popover closes on success.
    await waitFor(() => {
      expect(screen.queryByLabelText(/Flag note/)).not.toBeInTheDocument();
    });
  });

  it('omits the note field when the textarea is blank', async () => {
    const { fetchMock, requests } = captureFetchMock();
    render(<ControlBar sessionId="s-flag" status="live" />);

    fireEvent.click(screen.getByRole('button', { name: /Flag moment/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Save flag/ }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    expect(parseBody(requests[0]!)).toEqual({
      command_type: 'flag_moment',
      payload: {},
    });
  });
});

describe('<ControlBar /> — end session', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('requires two clicks (open confirm → confirm) before posting end_session', async () => {
    const { fetchMock, requests } = captureFetchMock();
    const onEnded = vi.fn();
    render(<ControlBar sessionId="s-end" status="live" onSessionEnded={onEnded} />);

    fireEvent.click(screen.getByRole('button', { name: 'End session' }));
    // No POST yet — confirm panel opens.
    expect(fetchMock).not.toHaveBeenCalled();
    const reasonInput = await screen.findByLabelText(/Reason/);
    fireEvent.change(reasonInput, { target: { value: 'participants dropped' } });

    // The end-confirm panel has its own "End session" button distinct from the
    // top-bar opener — pick the one inside the confirm panel.
    const confirmButtons = screen.getAllByRole('button', { name: /End session/ });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]!);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    expect(parseBody(requests[0]!)).toEqual({
      command_type: 'end_session',
      payload: { reason: 'participants dropped' },
    });
    expect(onEnded).toHaveBeenCalledTimes(1);
  });
});
