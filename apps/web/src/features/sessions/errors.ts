/**
 * Session-feature error types.
 *
 * Lives in its own module (rather than `service.ts`) so consumers
 * that only need to detect the error kinds — route handlers, the
 * command bus producer — can import them without dragging in the
 * LiveKit / token-mint side of `service.ts`. Keeps test mocks light
 * and avoids tripping the `server-only` guard inside livekit-server.
 */

export class SessionNotFoundError extends Error {
  readonly code = 'session_not_found';
  constructor(public readonly sessionId: string) {
    super(`Session ${sessionId} not found`);
    this.name = 'SessionNotFoundError';
  }
}

export class SessionAlreadyEndedError extends Error {
  readonly code = 'session_already_ended';
  constructor(public readonly sessionId: string) {
    super(`Session ${sessionId} has already ended`);
    this.name = 'SessionAlreadyEndedError';
  }
}
