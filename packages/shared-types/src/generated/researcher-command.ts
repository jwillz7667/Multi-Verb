/**
 * AUTO-GENERATED — DO NOT EDIT BY HAND.
 *
 * Source: Pydantic models in services/engine/verbio_engine/domain/.
 * Regenerate via: pnpm shared-types:generate
 */

/* eslint-disable */

export type CommandId = string;
export type CommandType =
  | 'force_prompt'
  | 'force_redirect'
  | 'force_summary'
  | 'whisper'
  | 'mute_moderator'
  | 'unmute_moderator'
  | 'pause_session'
  | 'resume_session'
  | 'set_quietness_budget'
  | 'flag_moment'
  | 'end_session';
export type IssuedAt = string;
export type ResearcherId = string;
export type SessionId = string;

/**
 * A single researcher-issued command targeting one session.
 */
export interface ResearcherCommand {
  command_id: CommandId;
  command_type: CommandType;
  issued_at: IssuedAt;
  payload?: Payload;
  researcher_id: ResearcherId;
  session_id: SessionId;
}
/**
 * Command-specific JSON payload; typed per command_type in Phase 5.
 */
export interface Payload {
  [k: string]: unknown | undefined;
}
