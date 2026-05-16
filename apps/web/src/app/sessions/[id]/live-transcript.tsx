'use client';

/**
 * LiveTranscript — researcher-facing live transcript view.
 *
 * Subscribes to `/api/sessions/[id]/events` via the browser's native
 * `EventSource`. The browser handles reconnect-with-Last-Event-ID for
 * us; the server route backfills any rows we missed from Postgres on
 * each reconnect, so the client doesn't need its own dedup or
 * resume logic — every event we see has already passed through
 * `seenIds` on the server.
 *
 * Phase 1 renders is_final and interim utterances in one feed, with
 * interim rows softened visually. Phase 2 will introduce participant
 * grouping, decision markers, and the moderator's utterances styled
 * distinctly.
 *
 * Why `EventSource` and not `fetch` + reader: EventSource gives us
 * automatic reconnect with Last-Event-ID for free across every modern
 * browser. The custom-fetch path would have to reimplement that with
 * exponential backoff and is exactly the kind of plumbing the brief
 * says not to reinvent (§ "do not invent a custom WebSocket
 * protocol" applies in spirit to SSE too).
 */

import { useEffect, useMemo, useRef, useState } from 'react';

// Direct module import — `@/features/sessions` re-exports server-only
// modules (repo, service), so a client component cannot import the
// barrel. The `events` module is pure schema + parser and is safe in
// the browser.
import { parseTranscriptEvent, type UtteranceTranscriptEvent } from '@/features/sessions/events';

interface Props {
  sessionId: string;
}

interface DisplayUtterance {
  id: string;
  participantId: string;
  participantDisplayName: string;
  text: string;
  isFinal: boolean;
  startTs: string;
}

type ConnectionState = 'connecting' | 'open' | 'closed';

const MAX_UTTERANCES = 500;

export function LiveTranscript({ sessionId }: Props): React.ReactElement {
  const [utterances, setUtterances] = useState<DisplayUtterance[]>([]);
  const [state, setState] = useState<ConnectionState>('connecting');
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const source = new EventSource(`/api/sessions/${sessionId}/events`);

    const onReady = (): void => {
      setState('open');
    };

    const onUtterance = (raw: MessageEvent<string>): void => {
      const parsed = parseTranscriptEvent(safeJsonParse(raw.data));
      // The SSE channel routes `state_snapshot` envelopes to a separate
      // listener; we still defensively narrow in case a stray message
      // is mis-routed (e.g., during a redeploy where the route is
      // forwarding everything as `utterance`).
      if (parsed?.type !== 'utterance') return;
      setUtterances((prev) => mergeUtterance(prev, toDisplay(parsed)));
    };

    const onOpen = (): void => {
      setState('open');
    };

    const onError = (): void => {
      // EventSource reconnects automatically; we just surface state.
      setState(source.readyState === EventSource.CLOSED ? 'closed' : 'connecting');
    };

    source.addEventListener('ready', onReady);
    source.addEventListener('utterance', onUtterance);
    source.addEventListener('open', onOpen);
    source.addEventListener('error', onError);

    return () => {
      source.removeEventListener('ready', onReady);
      source.removeEventListener('utterance', onUtterance);
      source.removeEventListener('open', onOpen);
      source.removeEventListener('error', onError);
      source.close();
    };
  }, [sessionId]);

  // Auto-scroll to the newest row so researchers don't have to chase
  // the transcript. Effect runs after each utterance render — the
  // scrollHeight is fresh at that point.
  useEffect(() => {
    const el = scrollRef.current;
    if (el === null) return;
    el.scrollTop = el.scrollHeight;
  }, [utterances]);

  const statusLabel = useMemo(() => {
    if (state === 'open') return 'Live';
    if (state === 'connecting') return 'Connecting…';
    return 'Disconnected';
  }, [state]);

  const statusDotClass =
    state === 'open'
      ? 'bg-green-500'
      : state === 'connecting'
        ? 'bg-yellow-500 animate-pulse'
        : 'bg-red-500';

  return (
    <section className="border-border-default bg-surface-primary flex flex-col gap-3 rounded-lg border p-6">
      <div className="flex items-center justify-between">
        <h2 className="text-text-primary text-lg font-medium">Live transcript</h2>
        <span className="text-text-secondary flex items-center gap-2 text-xs">
          <span className={`inline-block h-2 w-2 rounded-full ${statusDotClass}`} />
          {statusLabel}
        </span>
      </div>

      <div
        ref={scrollRef}
        className="border-border-default bg-surface-secondary max-h-96 min-h-32 overflow-y-auto rounded-md border p-3"
      >
        {utterances.length === 0 ? (
          <p className="text-text-tertiary text-sm italic">
            Waiting for the first utterance. Participants must join the room and start speaking.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {utterances.map((u) => (
              <li key={u.id} className="flex flex-col">
                <span className="text-text-secondary text-xs font-medium">
                  {u.participantDisplayName}
                  <span className="text-text-tertiary ml-2 font-mono">{formatTime(u.startTs)}</span>
                </span>
                <span className={u.isFinal ? 'text-text-primary' : 'text-text-tertiary italic'}>
                  {u.text}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function toDisplay(event: UtteranceTranscriptEvent): DisplayUtterance {
  return {
    id: event.payload.utterance_id,
    participantId: event.payload.participant_id,
    participantDisplayName: event.payload.participant_display_name,
    text: event.payload.text,
    isFinal: event.payload.is_final,
    startTs: event.payload.start_ts,
  };
}

/**
 * Merge an utterance into the rolling buffer.
 *
 * Backfill arrives ordered; live events arrive in publish order. We
 * append by default, but if the id already exists we replace in place
 * (interim → final transitions for the same utterance_id reuse the
 * same id from the engine's perspective). The cap keeps the DOM from
 * unbounded growth across a long session — older rows scroll off and
 * are re-fetched only on full reconnect.
 */
function mergeUtterance(prev: DisplayUtterance[], next: DisplayUtterance): DisplayUtterance[] {
  const existing = prev.findIndex((u) => u.id === next.id);
  if (existing >= 0) {
    const copy = prev.slice();
    copy[existing] = next;
    return copy;
  }
  const appended = [...prev, next];
  if (appended.length > MAX_UTTERANCES) {
    return appended.slice(appended.length - MAX_UTTERANCES);
  }
  return appended;
}

function safeJsonParse(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
