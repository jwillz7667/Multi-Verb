'use client';

/**
 * Composite-recording audio player + scrubber sync (brief §11.2).
 *
 * Source of truth is the shell's `currentTs`. Two-way sync:
 *
 *   - prop `currentTs` changes (timeline click, keyboard shortcut)
 *     → seek the underlying <audio> element if it has drifted past
 *     the tolerance,
 *   - <audio> `timeupdate` during playback → emit `onSeek(ts)` so
 *     the timeline cursor + state pane track audio playback.
 *
 * URL handling: we don't embed the signed R2 URL in the page payload
 * — the L4 route comment explains why (stale URL on a long-open tab
 * leaves the researcher with no recovery path). Instead the player
 * fetches `/api/sessions/[id]/recordings/audio` on mount, schedules a
 * refresh ~5 min before TTL expiry, and refetches on a media-error
 * event. The L4 endpoint is the "refresh endpoint" for this player.
 *
 * Keyboard shortcuts (when the page has focus, but never when an
 * input is focused — the typical "researcher is typing a note"
 * case):
 *
 *   - Space   → play / pause toggle (preventDefault so the page
 *     doesn't scroll),
 *   - ←  / →  → ±10 seconds (large skip for scanning),
 *   - J  / L  → ±2  seconds (fine-grained scrubbing).
 *
 * If `hasCompositeRecording` is false (egress is still running or
 * the session was aborted before any audio was produced) we render
 * a non-interactive "no recording yet" affordance and skip the
 * fetch entirely so we don't paint a 404 in DevTools on every page
 * load.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

interface Props {
  sessionId: string;
  hasCompositeRecording: boolean;
  sessionStart: string | null;
  sessionEnd: string | null;
  currentTs: string;
  onSeek: (ts: string) => void;
}

interface UrlState {
  url: string;
  expiresAt: string;
}

type FetchState = 'idle' | 'loading' | { url: string; expiresAt: string } | { error: ErrorCode };

type ErrorCode = 'no_recording' | 'r2_unavailable' | 'unauthorized' | 'unknown';

// Refresh the signed URL this far ahead of expiry. The L4 route TTL
// is 1h, so we refresh at ~55 minutes — gives the network plenty of
// margin without thrashing.
const URL_REFRESH_LEAD_MS = 5 * 60 * 1000;
// Seek tolerance — don't write back to `audio.currentTime` if the
// underlying element is already within this many seconds of where
// the prop says it should be. Without this the timeupdate→onSeek→
// prop→seek loop fires every tick.
const SEEK_TOLERANCE_SEC = 0.5;
// Throttle `onSeek` emissions to at most every 100ms during play —
// the audio `timeupdate` event fires up to 4× per second per spec,
// and the timeline cursor doesn't need finer resolution than that.
const EMIT_THROTTLE_MS = 100;
// Minimal valid WebVTT document; satisfies jsx-a11y/media-has-caption
// for the hidden <audio> without shipping a fake captions file.
// Real transcript is rendered by the transcript pane / export (L10).
const EMPTY_VTT_DATA_URI = 'data:text/vtt;base64,V0VCVlRUCg==';

export function ReplayAudioPlayer({
  sessionId,
  hasCompositeRecording,
  sessionStart,
  sessionEnd,
  currentTs,
  onSeek,
}: Props): React.ReactElement {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [fetchState, setFetchState] = useState<FetchState>(
    hasCompositeRecording ? 'idle' : { error: 'no_recording' },
  );
  const [isPlaying, setIsPlaying] = useState(false);
  const [durationSec, setDurationSec] = useState<number | null>(null);

  // Track the last ts we *emitted* via onSeek so we don't flood the
  // parent with millisecond-precision noise during playback.
  const lastEmittedRef = useRef<{ ts: string; at: number } | null>(null);

  // Coordinate prop-driven seeks vs user-driven seeks. When the prop
  // changes we write to audio.currentTime, which causes timeupdate to
  // fire. The flag prevents that ping from looping back to onSeek.
  const suppressEmitRef = useRef(false);

  const fetchSignedUrl = useCallback(async (): Promise<void> => {
    setFetchState((prev) => (typeof prev === 'object' && 'url' in prev ? prev : 'loading'));
    try {
      const res = await fetch(`/api/sessions/${sessionId}/recordings/audio`);
      if (res.status === 200) {
        const body = (await res.json()) as UrlState;
        setFetchState({ url: body.url, expiresAt: body.expiresAt });
        return;
      }
      if (res.status === 404) {
        setFetchState({ error: 'no_recording' });
        return;
      }
      if (res.status === 401) {
        setFetchState({ error: 'unauthorized' });
        return;
      }
      if (res.status === 503) {
        setFetchState({ error: 'r2_unavailable' });
        return;
      }
      setFetchState({ error: 'unknown' });
    } catch {
      setFetchState({ error: 'unknown' });
    }
  }, [sessionId]);

  // Initial fetch + scheduled refresh. When `hasCompositeRecording`
  // is false we never enter this effect — the initial state already
  // shows "no recording yet" and there's nothing to fetch.
  useEffect(() => {
    if (!hasCompositeRecording) return;
    void fetchSignedUrl();
  }, [fetchSignedUrl, hasCompositeRecording]);

  useEffect(() => {
    if (typeof fetchState !== 'object' || !('url' in fetchState)) return;
    const expiresMs = Date.parse(fetchState.expiresAt);
    if (Number.isNaN(expiresMs)) return;
    const refreshIn = Math.max(0, expiresMs - Date.now() - URL_REFRESH_LEAD_MS);
    const handle = setTimeout(() => {
      void fetchSignedUrl();
    }, refreshIn);
    return (): void => {
      clearTimeout(handle);
    };
  }, [fetchSignedUrl, fetchState]);

  const tsToOffsetSec = useCallback(
    (ts: string): number => {
      if (sessionStart === null) return 0;
      const offsetMs = Date.parse(ts) - Date.parse(sessionStart);
      if (Number.isNaN(offsetMs)) return 0;
      return Math.max(0, offsetMs / 1000);
    },
    [sessionStart],
  );

  const offsetSecToTs = useCallback(
    (offsetSec: number): string | null => {
      if (sessionStart === null) return null;
      const startMs = Date.parse(sessionStart);
      if (Number.isNaN(startMs)) return null;
      return new Date(startMs + Math.max(0, offsetSec) * 1000).toISOString();
    },
    [sessionStart],
  );

  // Sync the audio element to the prop. If the prop changed because
  // of the audio element's own timeupdate (during play), we'll already
  // be within tolerance and won't seek.
  useEffect(() => {
    const audio = audioRef.current;
    if (audio === null) return;
    const targetSec = tsToOffsetSec(currentTs);
    if (Math.abs(audio.currentTime - targetSec) > SEEK_TOLERANCE_SEC) {
      suppressEmitRef.current = true;
      audio.currentTime = targetSec;
    }
  }, [currentTs, tsToOffsetSec]);

  const emitSeekFromAudio = useCallback(
    (offsetSec: number): void => {
      if (suppressEmitRef.current) {
        suppressEmitRef.current = false;
        return;
      }
      const ts = offsetSecToTs(offsetSec);
      if (ts === null) return;
      const now = Date.now();
      const last = lastEmittedRef.current;
      if (last !== null && now - last.at < EMIT_THROTTLE_MS && last.ts === ts) {
        return;
      }
      lastEmittedRef.current = { ts, at: now };
      onSeek(ts);
    },
    [offsetSecToTs, onSeek],
  );

  const seekBySeconds = useCallback(
    (deltaSec: number): void => {
      // The prop is the source of truth — during playback the audio
      // element runs slightly ahead of the prop until the next
      // timeupdate flushes the value upward, but that drift is at
      // most one event tick and dwarfed by a 2-10s skip. Driving
      // off the prop also means the keyboard works before the audio
      // element has finished loading metadata.
      const baseSec = tsToOffsetSec(currentTs);
      const totalDurationSec =
        durationSec ??
        (sessionStart !== null && sessionEnd !== null
          ? Math.max(0, (Date.parse(sessionEnd) - Date.parse(sessionStart)) / 1000)
          : Number.POSITIVE_INFINITY);
      const nextSec = Math.max(0, Math.min(totalDurationSec, baseSec + deltaSec));
      const ts = offsetSecToTs(nextSec);
      if (ts === null) return;
      // Bypass throttle on user-initiated jumps so the cursor lands
      // immediately even if the user mashes the key.
      lastEmittedRef.current = { ts, at: Date.now() };
      onSeek(ts);
    },
    [currentTs, durationSec, offsetSecToTs, onSeek, sessionEnd, sessionStart, tsToOffsetSec],
  );

  const togglePlay = useCallback((): void => {
    const audio = audioRef.current;
    if (audio === null) return;
    if (!hasUrl(fetchState)) return;
    // Drive off React state, not `audio.paused`, because jsdom never
    // flips `paused` (stubbed play/pause don't mutate it) and even in
    // real browsers there's a brief window during media transitions
    // where the two disagree.
    if (isPlaying) {
      audio.pause();
      setIsPlaying(false);
    } else {
      setIsPlaying(true);
      // Real browsers' autoplay policy can reject play() — swallow so
      // the UI state isn't half-flipped on rejection. jsdom returns
      // Promise.resolve() (test stub), real browsers resolve once
      // playback actually begins.
      void audio.play().catch(() => {
        setIsPlaying(false);
      });
    }
  }, [fetchState, isPlaying]);

  // Keyboard shortcuts. Attached to document so the player works even
  // when focus is on the page chrome — but skipped when the user is
  // typing in an input/textarea/contenteditable region.
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (isTypingTarget(event.target)) return;
      switch (event.key) {
        case ' ':
        case 'Spacebar':
          event.preventDefault();
          togglePlay();
          break;
        case 'ArrowLeft':
          event.preventDefault();
          seekBySeconds(-10);
          break;
        case 'ArrowRight':
          event.preventDefault();
          seekBySeconds(10);
          break;
        case 'j':
        case 'J':
          event.preventDefault();
          seekBySeconds(-2);
          break;
        case 'l':
        case 'L':
          event.preventDefault();
          seekBySeconds(2);
          break;
        default:
          break;
      }
    };
    document.addEventListener('keydown', onKey);
    return (): void => {
      document.removeEventListener('keydown', onKey);
    };
  }, [seekBySeconds, togglePlay]);

  const summaryText =
    sessionStart !== null && sessionEnd !== null
      ? formatPlaybackTime(tsToOffsetSec(currentTs), durationFromSession(sessionStart, sessionEnd))
      : formatPlaybackTime(tsToOffsetSec(currentTs), durationSec);

  const status = renderStatus(fetchState, hasCompositeRecording);

  return (
    <>
      <div className="flex items-center gap-3">
        <button
          type="button"
          className="border-border-default text-text-primary hover:bg-surface-tertiary inline-flex h-9 w-9 items-center justify-center rounded-md border text-sm disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!hasUrl(fetchState)}
          onClick={(): void => {
            seekBySeconds(-10);
          }}
          data-testid="replay-audio-skip-back"
          aria-label="Skip back 10 seconds"
        >
          ⟲10
        </button>
        <button
          type="button"
          className="bg-accent text-accent-fg hover:bg-accent inline-flex h-9 w-9 items-center justify-center rounded-md text-sm disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!hasUrl(fetchState)}
          onClick={togglePlay}
          data-testid="replay-audio-play"
          aria-label={isPlaying ? 'Pause' : 'Play'}
        >
          {isPlaying ? '❚❚' : '▶︎'}
        </button>
        <button
          type="button"
          className="border-border-default text-text-primary hover:bg-surface-tertiary inline-flex h-9 w-9 items-center justify-center rounded-md border text-sm disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!hasUrl(fetchState)}
          onClick={(): void => {
            seekBySeconds(10);
          }}
          data-testid="replay-audio-skip-forward"
          aria-label="Skip forward 10 seconds"
        >
          10⟳
        </button>
        <div
          className="text-text-secondary ml-2 font-mono text-xs tabular-nums"
          data-testid="replay-audio-time"
        >
          {summaryText}
        </div>
        {status !== null ? (
          <div className="text-text-tertiary ml-auto text-xs" data-testid="replay-audio-status">
            {status}
          </div>
        ) : null}
      </div>

      {/* Hidden <audio>: we render our own controls above and use the
          element imperatively. `preload="metadata"` so duration is
          available without buffering full audio on page load. The
          empty captions track is intentional — captions belong on
          the transcript pane (L10); this satisfies a11y lint without
          shipping a misleading caption file. */}
      {hasUrl(fetchState) ? (
        <audio
          ref={audioRef}
          src={fetchState.url}
          preload="metadata"
          className="hidden"
          data-testid="replay-audio-element"
          onLoadedMetadata={(e): void => {
            const el = e.currentTarget;
            if (!Number.isNaN(el.duration) && Number.isFinite(el.duration)) {
              setDurationSec(el.duration);
            }
          }}
          onTimeUpdate={(e): void => {
            emitSeekFromAudio(e.currentTarget.currentTime);
          }}
          onPlay={(): void => {
            setIsPlaying(true);
          }}
          onPause={(): void => {
            setIsPlaying(false);
          }}
          onEnded={(): void => {
            setIsPlaying(false);
          }}
          onError={(): void => {
            // Most likely cause is signature expiry → refetch and let
            // the new URL replace `src`. The audio element will retry
            // automatically once `src` changes.
            void fetchSignedUrl();
          }}
        >
          <track kind="captions" srcLang="en" src={EMPTY_VTT_DATA_URI} />
        </audio>
      ) : null}
    </>
  );
}

function hasUrl(state: FetchState): state is { url: string; expiresAt: string } {
  return typeof state === 'object' && 'url' in state;
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (target === null) return false;
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (target.isContentEditable) return true;
  return false;
}

function renderStatus(state: FetchState, hasCompositeRecording: boolean): string | null {
  if (!hasCompositeRecording) return 'no recording yet — egress may still be running';
  if (state === 'idle' || state === 'loading') return 'loading audio…';
  if ('error' in state) {
    switch (state.error) {
      case 'no_recording':
        return 'recording not available';
      case 'r2_unavailable':
        return 'storage unavailable — ops triage';
      case 'unauthorized':
        return 'unauthorized — sign in again';
      case 'unknown':
        return 'audio fetch failed';
    }
  }
  return null;
}

function durationFromSession(start: string, end: string): number | null {
  const startMs = Date.parse(start);
  const endMs = Date.parse(end);
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return null;
  return Math.max(0, (endMs - startMs) / 1000);
}

function formatPlaybackTime(currentSec: number, totalSec: number | null): string {
  const cur = formatSeconds(currentSec);
  if (totalSec === null || !Number.isFinite(totalSec)) return cur;
  return `${cur} / ${formatSeconds(totalSec)}`;
}

function formatSeconds(sec: number): string {
  const safe = Math.max(0, Math.floor(sec));
  const m = Math.floor(safe / 60);
  const s = safe % 60;
  return `${m.toString()}:${s.toString().padStart(2, '0')}`;
}
