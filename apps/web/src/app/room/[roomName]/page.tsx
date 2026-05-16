/**
 * /room/[roomName] — LiveKit join surface for any role.
 *
 * Server component resolves the session by room name and hands off to
 * a client component (LiveKitRoom needs `useEffect` for device access).
 * Participants are not auth-gated; researchers are. The role + session
 * id arrive via query params from the dashboard.
 */

import { notFound } from 'next/navigation';

import { db } from '@/lib/db';

import { RoomJoinClient } from './room-join-client';

export const dynamic = 'force-dynamic';

interface PageProps {
  params: Promise<{ roomName: string }>;
  searchParams: Promise<{ role?: string; name?: string; sessionId?: string }>;
}

export default async function RoomPage({
  params,
  searchParams,
}: PageProps): Promise<React.ReactElement> {
  const { roomName } = await params;
  const { role: roleParam, name: nameParam, sessionId: sessionIdParam } = await searchParams;

  const session = await db.moderatedSession.findUnique({
    where: { livekitRoomName: roomName },
    select: { id: true, livekitRoomName: true, status: true },
  });
  if (session === null) notFound();

  const requestedRole =
    roleParam === 'researcher' || roleParam === 'moderator' ? roleParam : 'participant';

  return (
    <RoomJoinClient
      sessionId={sessionIdParam ?? session.id}
      roomName={session.livekitRoomName}
      initialRole={requestedRole}
      initialName={nameParam ?? ''}
      initialStatus={session.status}
    />
  );
}
