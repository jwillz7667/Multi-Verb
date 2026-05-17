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
 *
 * Wire shape matches brief §5.4 verbatim. The post-init validator
 * asserts `payload` conforms to the typed model for `command_type`;
 * `payload` itself stays an open `dict[str, Any]` to keep the JSON
 * Schema (and thus the generated TypeScript) one-to-one with what the
 * web producer sends.
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
 * Command-specific JSON payload; validated against the typed model for command_type.
 */
export interface Payload {
  [k: string]: unknown | undefined;
}
