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
export { publishResearcherCommand, PublishCommandInputSchema } from './commands';
export type { PublishCommandInput } from './commands';
export {
  findDecisionSessionId,
  findSessionById,
  findSessionForReplay,
  findSnapshotAtOrBefore,
  listDecisionsByRange,
  listDecisionsSince,
  listParticipantsForSession,
  listRecentSessions,
  listRuleEvaluationsForDecision,
  listSessionFlagsByRange,
  listSnapshotsByRange,
  listStateSnapshotsSince,
  listUtterancesByRange,
  listUtterancesSince,
} from './repo';
export type {
  DecisionRow,
  ModeratedSessionRow,
  ParticipantRow,
  ReplaySessionRow,
  RuleEvaluationRow,
  SessionFlagRow,
  StateSnapshotRow,
  UtteranceWithSpeakerRow,
} from './repo';
export { parseTranscriptEvent, transcriptEventSchema } from './events';
export type {
  DecisionTranscriptEvent,
  ModeratorDecisionValidated,
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
