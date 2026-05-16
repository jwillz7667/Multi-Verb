'use client';

import '@livekit/components-styles';

import {
  ControlBar,
  GridLayout,
  LiveKitRoom,
  ParticipantTile,
  RoomAudioRenderer,
  useTracks,
} from '@livekit/components-react';
import { Track } from 'livekit-client';
import { useState, type SyntheticEvent } from 'react';

import type { ParticipantRole } from '@/features/sessions';

interface Props {
  sessionId: string;
  roomName: string;
  initialRole: ParticipantRole;
  initialName: string;
  initialStatus: string;
}

interface TokenResponse {
  token: string;
  url: string;
  identity: string;
  role: ParticipantRole;
}

export function RoomJoinClient({
  sessionId,
  roomName,
  initialRole,
  initialName,
  initialStatus,
}: Props): React.ReactElement {
  const [displayName, setDisplayName] = useState(initialName);
  const [role] = useState<ParticipantRole>(initialRole);
  const [connection, setConnection] = useState<TokenResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [joining, setJoining] = useState(false);

  async function onJoin(event: SyntheticEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const trimmed = displayName.trim();
    if (trimmed.length === 0) {
      setError('Display name required');
      return;
    }
    setJoining(true);
    setError(null);
    try {
      const res = await fetch('/api/livekit/token', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          sessionId,
          displayName: trimmed,
          role,
        }),
      });
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(detail.error ?? `token request failed (${res.status})`);
      }
      setConnection((await res.json()) as TokenResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'unexpected error');
      setJoining(false);
    }
  }

  if (connection !== null) {
    return (
      <main className="flex h-screen flex-col bg-black text-white">
        <header className="flex items-center justify-between border-b border-white/10 px-4 py-2 text-sm">
          <span className="font-mono text-xs opacity-70">{roomName}</span>
          <span className="opacity-70">
            {connection.identity} ({connection.role})
          </span>
        </header>
        <LiveKitRoom
          serverUrl={connection.url}
          token={connection.token}
          connect
          audio
          video={false}
          data-lk-theme="default"
          className="flex-1"
        >
          <RoomAudioRenderer />
          <ParticipantGrid />
          <ControlBar
            variation="minimal"
            controls={{ camera: false, microphone: true, screenShare: false, chat: false }}
          />
        </LiveKitRoom>
      </main>
    );
  }

  return (
    <main className="bg-surface-secondary text-text-primary min-h-screen">
      <div className="mx-auto flex max-w-md flex-col gap-6 px-6 py-16">
        <header>
          <h1 className="text-2xl font-medium tracking-tight">Join session</h1>
          <p className="text-text-secondary mt-1 text-sm">
            Room <span className="font-mono text-xs">{roomName}</span> · status{' '}
            <span className="font-mono text-xs">{initialStatus}</span>
          </p>
        </header>

        <form
          onSubmit={(e) => {
            void onJoin(e);
          }}
          className="border-border-default bg-surface-primary flex flex-col gap-4 rounded-lg border p-6"
        >
          <label className="flex flex-col gap-2 text-sm">
            <span className="text-text-primary font-medium">Display name</span>
            <input
              type="text"
              value={displayName}
              onChange={(e) => {
                setDisplayName(e.target.value);
              }}
              placeholder="What should others call you?"
              className="border-border-default rounded-md border bg-transparent px-3 py-2 text-sm"
            />
          </label>
          <p className="text-text-tertiary text-xs">
            You&apos;ll join as <strong>{role}</strong>. Microphone access is requested on join;
            video is off for Phase 1.
          </p>

          {error !== null && (
            <p className="rounded-md bg-red-100 px-3 py-2 text-sm text-red-900">{error}</p>
          )}

          <button
            type="submit"
            disabled={joining}
            className="bg-text-primary text-surface-primary rounded-md px-4 py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >
            {joining ? 'Joining…' : 'Join room'}
          </button>
        </form>
      </div>
    </main>
  );
}

function ParticipantGrid(): React.ReactElement {
  const tracks = useTracks(
    [
      { source: Track.Source.Microphone, withPlaceholder: true },
      { source: Track.Source.Camera, withPlaceholder: false },
    ],
    { onlySubscribed: false },
  );
  return (
    <GridLayout tracks={tracks} style={{ height: 'calc(100% - 80px)', padding: '16px' }}>
      <ParticipantTile />
    </GridLayout>
  );
}
