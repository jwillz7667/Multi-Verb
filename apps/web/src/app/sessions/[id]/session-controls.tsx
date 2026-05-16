'use client';

import { useState } from 'react';

import type { SessionStatus } from '@/features/sessions';

interface Props {
  sessionId: string;
  roomName: string;
  inviteUrl: string;
  initialStatus: SessionStatus;
}

export function SessionLifecycleControls({
  sessionId,
  roomName,
  inviteUrl,
  initialStatus,
}: Props): React.ReactElement {
  const [status, setStatus] = useState<SessionStatus>(initialStatus);
  const [dispatching, setDispatching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [researcherName, setResearcherName] = useState('Researcher');

  async function onStart(): Promise<void> {
    setDispatching(true);
    setError(null);
    try {
      const res = await fetch(`/api/sessions/${sessionId}/start`, { method: 'POST' });
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(detail.error ?? `dispatch failed (${res.status})`);
      }
      // Engine flips the row to "live" via its own connect handler;
      // we optimistically reflect that here so the button updates
      // immediately. A real-time subscription comes in L5 (SSE).
      setStatus('live');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'unexpected error');
    } finally {
      setDispatching(false);
    }
  }

  const researcherJoinHref = `/room/${encodeURIComponent(roomName)}?role=researcher&name=${encodeURIComponent(researcherName)}&sessionId=${sessionId}`;
  const isTerminal = status === 'ended' || status === 'aborted';

  return (
    <section className="border-border-default bg-surface-primary flex flex-col gap-4 rounded-lg border p-6">
      <div>
        <h2 className="text-text-primary text-lg font-medium">Moderator</h2>
        <p className="text-text-secondary mt-1 text-sm">
          Dispatches the agent (<code className="font-mono text-xs">verbio-moderator</code>) into
          the LiveKit room. Idempotent — safe to retry.
        </p>
        <button
          type="button"
          onClick={() => {
            void onStart();
          }}
          disabled={dispatching || isTerminal}
          className="bg-text-primary text-surface-primary mt-3 rounded-md px-4 py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50"
        >
          {dispatching ? 'Dispatching…' : status === 'live' ? 'Re-dispatch agent' : 'Start session'}
        </button>
      </div>

      <hr className="border-border-default" />

      <div className="flex flex-col gap-3">
        <h2 className="text-text-primary text-lg font-medium">Join as researcher</h2>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-text-secondary">Display name</span>
          <input
            type="text"
            value={researcherName}
            onChange={(e) => {
              setResearcherName(e.target.value);
            }}
            className="border-border-default rounded-md border bg-transparent px-3 py-2 text-sm"
          />
        </label>
        <a
          href={researcherJoinHref}
          className="border-border-default hover:bg-surface-tertiary inline-flex items-center justify-center rounded-md border px-4 py-2 text-sm font-medium transition-colors"
        >
          Open room
        </a>
      </div>

      <hr className="border-border-default" />

      <div>
        <h2 className="text-text-primary text-lg font-medium">Participant invite</h2>
        <p className="text-text-secondary mt-1 text-sm">
          Share this URL with participants. They&apos;ll be prompted for a display name when they
          land on the join page.
        </p>
        <code className="text-text-primary bg-surface-secondary mt-2 block break-all rounded-md px-3 py-2 font-mono text-xs">
          {inviteUrl}
        </code>
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard.writeText(inviteUrl);
          }}
          className="text-text-secondary hover:text-text-primary mt-2 text-xs underline"
        >
          Copy
        </button>
      </div>

      {error !== null && (
        <p className="rounded-md bg-red-100 px-3 py-2 text-sm text-red-900">{error}</p>
      )}
    </section>
  );
}
