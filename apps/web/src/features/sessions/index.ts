/**
 * Sessions feature — public surface.
 *
 * Consumers (route handlers, server actions, UI server components)
 * import from this barrel. Deep imports across feature boundaries
 * are forbidden per the project's architecture rules.
 */

export {
  createNewSession,
  issueJoinToken,
  SessionAlreadyEndedError,
  SessionNotFoundError,
  startSession,
} from './service';
export { findSessionById, listRecentSessions } from './repo';
export type { ModeratedSessionRow } from './repo';
export {
  CreateSessionInputSchema,
  MintTokenInputSchema,
  PARTICIPANT_ROLES,
  SESSION_STATUSES,
} from './types';
export type {
  CreatedSession,
  CreateSessionInput,
  MintTokenInput,
  ParticipantRole,
  SessionStatus,
} from './types';
