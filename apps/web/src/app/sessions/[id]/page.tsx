/**
 * /sessions/[id] — session detail + join controls.
 *
 * Shows lifecycle metadata and two affordances:
 *   - Start session: dispatches the agent into the LiveKit room.
 *   - Join as researcher / participant: links to /room/[name] with
 *     the role + display name passed via query string (the join page
 *     is a client component that mints its own token via the API).
 *
 * The participant invite URL is also surfaced so the researcher can
 * share it out-of-band. Phase 2 will replace this with signed invites.
 */

import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';

import { findSessionById } from '@/features/sessions';
import { auth } from '@/lib/auth';
import { clientEnv } from '@/lib/env';
import { orgIdForUser } from '@/lib/identity';
import { scopedDb } from '@/lib/scoped-db';

import { ControlBar } from './control-bar';
import { DecisionLog } from './decision-log';
import { InterventionModals } from './intervention-modals';
import { LiveTranscript } from './live-transcript';
import { ParticipantTiles } from './participant-tiles';
import { SessionLifecycleControls } from './session-controls';

export const dynamic = 'force-dynamic';

interface PageProps {
  params: Promise<{ id: string }>;
}

export default async function SessionDetailPage({
  params,
}: PageProps): Promise<React.ReactElement> {
  const userSession = await auth();
  const { id } = await params;
  if (!userSession?.user?.id) {
    redirect(`/sign-in?callbackUrl=/sessions/${id}`);
  }

  // Tenancy gate: confirm session ownership before loading any
  // per-session data. Cross-org access becomes a 404, matching the
  // replay page and the export routes — the live detail page would
  // otherwise leak per-session metadata (room name, status, timestamps)
  // through the rendered HTML.
  const orgId = orgIdForUser(userSession.user.id);
  const owned = await scopedDb(orgId).sessions.findById(id);
  if (owned === null) notFound();

  const session = await findSessionById(id);
  if (session === null) notFound();

  const inviteUrl = `${clientEnv.NEXT_PUBLIC_APP_URL}/room/${session.livekitRoomName}`;

  return (
    <main className="min-h-screen bg-surface-secondary text-text-primary">
      <div className="mx-auto flex max-w-3xl flex-col gap-6 px-6 py-12">
        <div className="flex items-center justify-between">
          <Link href="/sessions" className="text-text-secondary text-sm hover:underline">
            ← All sessions
          </Link>
          <div className="flex items-center gap-4">
            <Link
              href={`/sessions/${session.id}/replay`}
              className="text-text-secondary text-sm hover:underline"
            >
              Replay →
            </Link>
            <span className="text-text-tertiary font-mono text-xs">{session.id}</span>
          </div>
        </div>

        <header>
          <h1 className="text-2xl font-medium tracking-tight">{session.livekitRoomName}</h1>
          <dl className="text-text-secondary mt-3 grid grid-cols-2 gap-y-2 text-sm">
            <dt>Status</dt>
            <dd className="font-mono">{session.status}</dd>
            <dt>Created</dt>
            <dd>{session.createdAt.toLocaleString()}</dd>
            {session.scheduledStart !== null && (
              <>
                <dt>Scheduled start</dt>
                <dd>{session.scheduledStart.toLocaleString()}</dd>
              </>
            )}
            {session.actualStart !== null && (
              <>
                <dt>Started</dt>
                <dd>{session.actualStart.toLocaleString()}</dd>
              </>
            )}
            {session.actualEnd !== null && (
              <>
                <dt>Ended</dt>
                <dd>{session.actualEnd.toLocaleString()}</dd>
              </>
            )}
          </dl>
        </header>

        <SessionLifecycleControls
          sessionId={session.id}
          roomName={session.livekitRoomName}
          inviteUrl={inviteUrl}
          initialStatus={session.status}
        />

        <ControlBar sessionId={session.id} status={session.status} />
        <InterventionModals sessionId={session.id} status={session.status} />

        <ParticipantTiles sessionId={session.id} />
        <LiveTranscript sessionId={session.id} />
        <DecisionLog sessionId={session.id} />
      </div>
    </main>
  );
}
