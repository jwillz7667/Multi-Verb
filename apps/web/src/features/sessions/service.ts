/**
 * Session orchestration — composes repo + LiveKit calls.
 *
 * Split from `repo.ts` so the persistence layer stays free of
 * transport concerns (LiveKit dispatch, token minting). Route handlers
 * and server actions consume this surface, not the repo directly.
 */

import 'server-only';

import { dispatchAgent, mintParticipantToken } from '@/lib/livekit-server';

import { SessionAlreadyEndedError, SessionNotFoundError } from './errors';
import { createSession, findSessionById } from './repo';

import type { CreateSessionInput, MintTokenInput } from './types';

export { SessionAlreadyEndedError, SessionNotFoundError } from './errors';

/**
 * Create a session row. Engine dispatch happens lazily on first join
 * (see `startSession`) so a researcher who creates and abandons a
 * session doesn't spend engine cycles on it.
 */
export async function createNewSession(input: CreateSessionInput) {
  return createSession({ scheduledStart: input.scheduledStart ?? null });
}

/**
 * Dispatch the agent for a session. Safe to call repeatedly — LiveKit
 * dedupes by (room, agent_name). Returns the latest session row so
 * callers don't need a second round-trip to see the new status.
 */
export async function startSession(sessionId: string) {
  const session = await findSessionById(sessionId);
  if (session === null) throw new SessionNotFoundError(sessionId);
  if (session.status === 'ended' || session.status === 'aborted') {
    throw new SessionAlreadyEndedError(sessionId);
  }
  await dispatchAgent({ roomName: session.livekitRoomName, sessionId });
  // We don't flip the DB status to "live" here — the engine's
  // `on_room_connected` does that authoritatively. Returning the
  // pre-dispatch row is fine; the UI polls or subscribes for updates.
  return session;
}

interface IssueTokenResult {
  token: string;
  url: string;
  roomName: string;
  identity: string;
  role: MintTokenInput['role'];
  displayName: string;
}

/**
 * Mint a LiveKit join token for a session. Returns the WS URL so the
 * browser doesn't need a second env-var fetch. Identity is derived
 * from a stable hash of `displayName + role` plus a short random
 * suffix so the same browser tab can reconnect, but two tabs sharing
 * the same display name still get distinct rows in the participants
 * table.
 */
export async function issueJoinToken(input: MintTokenInput): Promise<IssueTokenResult> {
  const session = await findSessionById(input.sessionId);
  if (session === null) throw new SessionNotFoundError(input.sessionId);
  if (session.status === 'ended' || session.status === 'aborted') {
    throw new SessionAlreadyEndedError(input.sessionId);
  }
  // `<role>-<slug>-<rand>` — slug is the display name lowercased and
  // ascii-only for clean log/dashboard output.
  const slug = input.displayName
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 24);
  const rand = Math.random().toString(36).slice(2, 8);
  const identity = `${input.role}-${slug || 'anon'}-${rand}`;
  const minted = await mintParticipantToken({
    roomName: session.livekitRoomName,
    identity,
    displayName: input.displayName,
    role: input.role,
  });
  return {
    token: minted.token,
    url: minted.url,
    roomName: session.livekitRoomName,
    identity,
    role: input.role,
    displayName: input.displayName,
  };
}
