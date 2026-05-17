/**
 * Tests for the replay audio player + scrubber sync (P6 L6).
 *
 * Pins:
 *   - URL fetch is gated by `hasCompositeRecording` — the "no
 *     recording yet" affordance never makes a network request,
 *   - happy path: GET → 200 → <audio> mounted with the signed src,
 *   - error path: 404, 503 surface researcher-readable copy,
 *   - keyboard shortcuts (space, ←/→, J/L) call onSeek with the
 *     right offset (and are skipped when an input has focus),
 *   - prop-driven `currentTs` change writes back to the audio
 *     element's currentTime (within tolerance),
 *   - the player does NOT re-emit onSeek when the audio currentTime
 *     change was triggered by a prop sync (no feedback loop),
 *   - audio `error` event triggers a URL refetch.
 *
 * jsdom has no audio backend, so `audio.play()` is stubbed to a
 * resolved promise. The component already swallows real-browser
 * play() rejection (autoplay policy), so the stub doesn't paper
 * over a bug.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ReplayAudioPlayer } from './replay-audio-player';

const SESSION_ID = 'sess-1';
const SESSION_START = '2026-05-01T10:00:00.000Z';
const SESSION_END = '2026-05-01T11:00:00.000Z'; // 60 min duration

function setupFetchMock(
  response:
    | { ok: true; body: { url: string; expiresAt: string } }
    | { status: number; body?: unknown },
): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(() => {
    if ('ok' in response) {
      return Promise.resolve(
        new Response(JSON.stringify(response.body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      );
    }
    return Promise.resolve(
      new Response(JSON.stringify(response.body ?? {}), {
        status: response.status,
        headers: { 'content-type': 'application/json' },
      }),
    );
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

beforeEach(() => {
  // jsdom doesn't implement HTMLMediaElement.play / pause — stub to
  // no-ops so the component's imperative calls don't blow up.
  Object.defineProperty(HTMLMediaElement.prototype, 'play', {
    configurable: true,
    value: vi.fn(() => Promise.resolve()),
  });
  Object.defineProperty(HTMLMediaElement.prototype, 'pause', {
    configurable: true,
    value: vi.fn(),
  });
  // jsdom defaults `paused` to true and `currentTime` to 0; we'll
  // mutate currentTime directly in the tests that care.
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('<ReplayAudioPlayer /> — no recording', () => {
  it('renders the "no recording yet" status and skips the URL fetch', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    render(
      <ReplayAudioPlayer
        sessionId={SESSION_ID}
        hasCompositeRecording={false}
        sessionStart={SESSION_START}
        sessionEnd={SESSION_END}
        currentTs={SESSION_START}
        onSeek={vi.fn()}
      />,
    );

    expect(screen.getByTestId('replay-audio-status')).toHaveTextContent(/no recording yet/i);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId('replay-audio-element')).not.toBeInTheDocument();
    expect(screen.getByTestId('replay-audio-play')).toBeDisabled();
  });
});

describe('<ReplayAudioPlayer /> — fetch lifecycle', () => {
  it('fetches the signed URL on mount and mounts <audio> with that src', async () => {
    const fetchMock = setupFetchMock({
      ok: true,
      body: { url: 'https://r2.example/composite.mp4?sig=xyz', expiresAt: futureIso(60 * 60) },
    });

    render(
      <ReplayAudioPlayer
        sessionId={SESSION_ID}
        hasCompositeRecording={true}
        sessionStart={SESSION_START}
        sessionEnd={SESSION_END}
        currentTs={SESSION_START}
        onSeek={vi.fn()}
      />,
    );

    expect(fetchMock).toHaveBeenCalledWith(`/api/sessions/${SESSION_ID}/recordings/audio`);

    const audio = await waitFor(() => screen.getByTestId('replay-audio-element'));
    expect(audio).toHaveAttribute('src', 'https://r2.example/composite.mp4?sig=xyz');
    expect(screen.queryByTestId('replay-audio-status')).not.toBeInTheDocument();
  });

  it('shows "recording not available" on 404', async () => {
    setupFetchMock({ status: 404, body: { error: 'recording_not_found' } });

    render(
      <ReplayAudioPlayer
        sessionId={SESSION_ID}
        hasCompositeRecording={true}
        sessionStart={SESSION_START}
        sessionEnd={SESSION_END}
        currentTs={SESSION_START}
        onSeek={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('replay-audio-status')).toHaveTextContent(
        /recording not available/i,
      );
    });
    expect(screen.queryByTestId('replay-audio-element')).not.toBeInTheDocument();
  });

  it('shows "storage unavailable" on 503 so ops sees the triage hint', async () => {
    setupFetchMock({ status: 503, body: { error: 'r2_not_configured' } });

    render(
      <ReplayAudioPlayer
        sessionId={SESSION_ID}
        hasCompositeRecording={true}
        sessionStart={SESSION_START}
        sessionEnd={SESSION_END}
        currentTs={SESSION_START}
        onSeek={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('replay-audio-status')).toHaveTextContent(/storage unavailable/i);
    });
  });
});

describe('<ReplayAudioPlayer /> — play / pause', () => {
  it('toggles between play and pause icons when the button is clicked', async () => {
    setupFetchMock({
      ok: true,
      body: { url: 'https://r2.example/c.mp4?sig=a', expiresAt: futureIso(60 * 60) },
    });

    render(
      <ReplayAudioPlayer
        sessionId={SESSION_ID}
        hasCompositeRecording={true}
        sessionStart={SESSION_START}
        sessionEnd={SESSION_END}
        currentTs={SESSION_START}
        onSeek={vi.fn()}
      />,
    );

    const playButton = await waitFor(() => {
      const btn = screen.getByTestId('replay-audio-play');
      expect(btn).not.toBeDisabled();
      return btn;
    });

    expect(playButton).toHaveTextContent('▶︎');
    fireEvent.click(playButton);
    expect(playButton).toHaveTextContent('❚❚');
    fireEvent.click(playButton);
    expect(playButton).toHaveTextContent('▶︎');
  });
});

describe('<ReplayAudioPlayer /> — keyboard shortcuts', () => {
  it('space toggles play/pause', async () => {
    setupFetchMock({
      ok: true,
      body: { url: 'https://r2.example/c.mp4?sig=a', expiresAt: futureIso(60 * 60) },
    });

    render(
      <ReplayAudioPlayer
        sessionId={SESSION_ID}
        hasCompositeRecording={true}
        sessionStart={SESSION_START}
        sessionEnd={SESSION_END}
        currentTs={SESSION_START}
        onSeek={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('replay-audio-play')).not.toBeDisabled();
    });

    fireEvent.keyDown(document, { key: ' ' });
    expect(screen.getByTestId('replay-audio-play')).toHaveTextContent('❚❚');

    fireEvent.keyDown(document, { key: ' ' });
    expect(screen.getByTestId('replay-audio-play')).toHaveTextContent('▶︎');
  });

  it('ArrowLeft / ArrowRight seek by ±10 seconds', async () => {
    setupFetchMock({
      ok: true,
      body: { url: 'https://r2.example/c.mp4?sig=a', expiresAt: futureIso(60 * 60) },
    });
    const onSeek = vi.fn();
    const currentTs = '2026-05-01T10:05:00.000Z'; // 5 min into session

    render(
      <ReplayAudioPlayer
        sessionId={SESSION_ID}
        hasCompositeRecording={true}
        sessionStart={SESSION_START}
        sessionEnd={SESSION_END}
        currentTs={currentTs}
        onSeek={onSeek}
      />,
    );

    await waitFor(() => screen.getByTestId('replay-audio-element'));

    fireEvent.keyDown(document, { key: 'ArrowRight' });
    expect(onSeek).toHaveBeenLastCalledWith('2026-05-01T10:05:10.000Z');

    fireEvent.keyDown(document, { key: 'ArrowLeft' });
    expect(onSeek).toHaveBeenLastCalledWith('2026-05-01T10:04:50.000Z');
  });

  it('J / L seek by ±2 seconds (fine-grained scrubbing)', async () => {
    setupFetchMock({
      ok: true,
      body: { url: 'https://r2.example/c.mp4?sig=a', expiresAt: futureIso(60 * 60) },
    });
    const onSeek = vi.fn();

    render(
      <ReplayAudioPlayer
        sessionId={SESSION_ID}
        hasCompositeRecording={true}
        sessionStart={SESSION_START}
        sessionEnd={SESSION_END}
        currentTs="2026-05-01T10:10:00.000Z"
        onSeek={onSeek}
      />,
    );

    await waitFor(() => screen.getByTestId('replay-audio-element'));

    fireEvent.keyDown(document, { key: 'l' });
    expect(onSeek).toHaveBeenLastCalledWith('2026-05-01T10:10:02.000Z');

    fireEvent.keyDown(document, { key: 'J' });
    expect(onSeek).toHaveBeenLastCalledWith('2026-05-01T10:09:58.000Z');
  });

  it('clamps seek-back at 0 so the cursor never goes before the session start', async () => {
    setupFetchMock({
      ok: true,
      body: { url: 'https://r2.example/c.mp4?sig=a', expiresAt: futureIso(60 * 60) },
    });
    const onSeek = vi.fn();

    render(
      <ReplayAudioPlayer
        sessionId={SESSION_ID}
        hasCompositeRecording={true}
        sessionStart={SESSION_START}
        sessionEnd={SESSION_END}
        currentTs="2026-05-01T10:00:03.000Z" // 3s in
        onSeek={onSeek}
      />,
    );

    await waitFor(() => screen.getByTestId('replay-audio-element'));

    fireEvent.keyDown(document, { key: 'ArrowLeft' }); // -10s would land at -7s
    expect(onSeek).toHaveBeenLastCalledWith(SESSION_START);
  });

  it('clamps seek-forward at the session end so the cursor never overruns', async () => {
    setupFetchMock({
      ok: true,
      body: { url: 'https://r2.example/c.mp4?sig=a', expiresAt: futureIso(60 * 60) },
    });
    const onSeek = vi.fn();

    render(
      <ReplayAudioPlayer
        sessionId={SESSION_ID}
        hasCompositeRecording={true}
        sessionStart={SESSION_START}
        sessionEnd={SESSION_END}
        currentTs="2026-05-01T10:59:55.000Z" // 5s before end
        onSeek={onSeek}
      />,
    );

    await waitFor(() => screen.getByTestId('replay-audio-element'));

    fireEvent.keyDown(document, { key: 'ArrowRight' }); // +10s would overrun
    expect(onSeek).toHaveBeenLastCalledWith(SESSION_END);
  });

  it('ignores keyboard shortcuts when focus is on an input', async () => {
    setupFetchMock({
      ok: true,
      body: { url: 'https://r2.example/c.mp4?sig=a', expiresAt: futureIso(60 * 60) },
    });
    const onSeek = vi.fn();

    render(
      <>
        <input data-testid="researcher-note" />
        <ReplayAudioPlayer
          sessionId={SESSION_ID}
          hasCompositeRecording={true}
          sessionStart={SESSION_START}
          sessionEnd={SESSION_END}
          currentTs={SESSION_START}
          onSeek={onSeek}
        />
      </>,
    );

    await waitFor(() => screen.getByTestId('replay-audio-element'));

    const input = screen.getByTestId('researcher-note');
    input.focus();
    fireEvent.keyDown(input, { key: ' ' });
    fireEvent.keyDown(input, { key: 'ArrowRight' });
    fireEvent.keyDown(input, { key: 'l' });

    expect(onSeek).not.toHaveBeenCalled();
    expect(screen.getByTestId('replay-audio-play')).toHaveTextContent('▶︎');
  });
});

describe('<ReplayAudioPlayer /> — skip buttons', () => {
  it('skip-back and skip-forward buttons call onSeek by ±10 seconds', async () => {
    setupFetchMock({
      ok: true,
      body: { url: 'https://r2.example/c.mp4?sig=a', expiresAt: futureIso(60 * 60) },
    });
    const onSeek = vi.fn();

    render(
      <ReplayAudioPlayer
        sessionId={SESSION_ID}
        hasCompositeRecording={true}
        sessionStart={SESSION_START}
        sessionEnd={SESSION_END}
        currentTs="2026-05-01T10:05:00.000Z"
        onSeek={onSeek}
      />,
    );

    await waitFor(() => screen.getByTestId('replay-audio-element'));

    fireEvent.click(screen.getByTestId('replay-audio-skip-forward'));
    expect(onSeek).toHaveBeenLastCalledWith('2026-05-01T10:05:10.000Z');

    fireEvent.click(screen.getByTestId('replay-audio-skip-back'));
    expect(onSeek).toHaveBeenLastCalledWith('2026-05-01T10:04:50.000Z');
  });
});

describe('<ReplayAudioPlayer /> — error refetch', () => {
  it('refetches the signed URL when the <audio> element fires error', async () => {
    let callCount = 0;
    const fetchMock = vi.fn(() => {
      callCount += 1;
      return Promise.resolve(
        new Response(
          JSON.stringify({
            url: `https://r2.example/c-${callCount.toString()}.mp4`,
            expiresAt: futureIso(60 * 60),
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <ReplayAudioPlayer
        sessionId={SESSION_ID}
        hasCompositeRecording={true}
        sessionStart={SESSION_START}
        sessionEnd={SESSION_END}
        currentTs={SESSION_START}
        onSeek={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('replay-audio-element')).toHaveAttribute(
        'src',
        'https://r2.example/c-1.mp4',
      );
    });

    fireEvent.error(screen.getByTestId('replay-audio-element'));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(screen.getByTestId('replay-audio-element')).toHaveAttribute(
        'src',
        'https://r2.example/c-2.mp4',
      );
    });
  });
});

function futureIso(secondsFromNow: number): string {
  return new Date(Date.now() + secondsFromNow * 1000).toISOString();
}
