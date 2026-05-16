/**
 * LiveKit server-side helpers — token minting + agent dispatch.
 *
 * Server-only because the API secret never leaves the server: tokens
 * are minted here and shipped to browsers as opaque JWTs. The browser
 * uses LiveKit's JS SDK (which only needs the WS URL + token) to join.
 *
 * Agent dispatch:
 *   The engine worker registers under `agent_name="verbio-moderator"`
 *   with auto-dispatch off (see services/engine/.../worker.py). Web
 *   must explicitly call AgentDispatchClient.createDispatch for each
 *   session, passing the session_id in `metadata` so the agent can
 *   resolve the DB row.
 */

import 'server-only';

import { AccessToken, AgentDispatchClient } from 'livekit-server-sdk';

import { serverEnv } from './env';

if (serverEnv === null) {
  throw new Error('livekit-server.ts must only be imported on the server');
}

const env = serverEnv;

export type LiveKitRole = 'participant' | 'researcher' | 'moderator';

interface MintTokenInput {
  roomName: string;
  identity: string;
  displayName: string;
  role: LiveKitRole;
  /**
   * Token lifetime in seconds. Default is 6 hours — long enough to cover
   * a full research session plus reconnect grace. Tokens are still bound
   * to the room name; an expired token requires a re-mint via web.
   */
  ttlSeconds?: number;
}

interface MintedToken {
  token: string;
  url: string;
  identity: string;
  role: LiveKitRole;
}

const DEFAULT_TTL_SECONDS = 6 * 60 * 60;

function requireLiveKitCredentials(): {
  apiKey: string;
  apiSecret: string;
  wsUrl: string;
} {
  const apiKey = env.LIVEKIT_API_KEY;
  const apiSecret = env.LIVEKIT_API_SECRET;
  const wsUrl = env.NEXT_PUBLIC_LIVEKIT_URL;
  if (!apiKey || !apiSecret) {
    throw new Error('LiveKit credentials missing — set LIVEKIT_API_KEY and LIVEKIT_API_SECRET');
  }
  if (!wsUrl) {
    throw new Error('NEXT_PUBLIC_LIVEKIT_URL must be set to mint participant tokens');
  }
  return { apiKey, apiSecret, wsUrl };
}

export async function mintParticipantToken(input: MintTokenInput): Promise<MintedToken> {
  const { apiKey, apiSecret, wsUrl } = requireLiveKitCredentials();

  const at = new AccessToken(apiKey, apiSecret, {
    identity: input.identity,
    name: input.displayName,
    ttl: input.ttlSeconds ?? DEFAULT_TTL_SECONDS,
    // Embedded in the participant's metadata blob; engine's runtime
    // parses this in `ParticipantSnapshot.from_livekit` to assign role
    // for the participants table. Default-deny: missing role → participant.
    metadata: JSON.stringify({ role: input.role }),
  });

  // Researchers and moderators may need data-channel publish rights
  // for live commands; participants get audio-only publish + subscribe
  // for everyone. We enable publish for all human roles for Phase 1
  // (mic + camera if they choose) and lock down further in later phases.
  at.addGrant({
    room: input.roomName,
    roomJoin: true,
    canPublish: input.role !== 'moderator',
    canSubscribe: true,
    canPublishData: input.role === 'researcher' || input.role === 'moderator',
  });

  const token = await at.toJwt();
  return { token, url: wsUrl, identity: input.identity, role: input.role };
}

interface DispatchAgentInput {
  roomName: string;
  sessionId: string;
}

/**
 * Tell LiveKit to spawn our agent (`verbio-moderator`) for the room.
 *
 * Idempotent on the LiveKit side: the dispatch is keyed by
 * (room_name, agent_name) and re-dispatching for an active room is a
 * no-op. We pass the session_id in metadata so the engine resolves
 * which DB row to bind to without having to round-trip back to web.
 */
export async function dispatchAgent(input: DispatchAgentInput): Promise<void> {
  const { apiKey, apiSecret } = requireLiveKitCredentials();
  // SDK requires the HTTP base URL, not the wss:// one. Accept either
  // LIVEKIT_URL (preferred, can be https://) or fall back to deriving
  // it from the public WS URL.
  const httpUrl = env.LIVEKIT_URL ?? env.NEXT_PUBLIC_LIVEKIT_URL?.replace(/^wss:/, 'https:');
  if (!httpUrl) {
    throw new Error('LIVEKIT_URL or NEXT_PUBLIC_LIVEKIT_URL must be set to dispatch agents');
  }
  const client = new AgentDispatchClient(httpUrl, apiKey, apiSecret);
  await client.createDispatch(input.roomName, 'verbio-moderator', {
    metadata: JSON.stringify({ session_id: input.sessionId }),
  });
}
