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
export {
  findSessionById,
  listRecentSessions,
  listStateSnapshotsSince,
  listUtterancesSince,
} from './repo';
export type { ModeratedSessionRow, StateSnapshotRow, UtteranceWithSpeakerRow } from './repo';
export { parseTranscriptEvent, transcriptEventSchema } from './events';
export type {
  StateSnapshotTranscriptEvent,
  TranscriptEventInput,
  TranscriptEventValidated,
  UtteranceTranscriptEvent,
} from './events';
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
