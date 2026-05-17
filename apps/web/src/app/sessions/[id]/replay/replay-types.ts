/**
 * Wire-shapes shared between the replay server component and the
 * client-side replay shell. Importable from both: no `server-only`
 * marker, no runtime dependencies.
 *
 * Why duplicate-looking types instead of re-using the repo `*Row`
 * shapes directly: the repo rows carry `Date` and `bigint` values
 * that don't survive a server→client serialization through Next's
 * payload format. The wire shapes below normalize to ISO strings and
 * plain numbers, the same way the SSE wire schema does — keeping
 * the client component free of date/bigint coercion bugs.
 */

export interface ReplayParticipantDTO {
  id: string;
  display_name: string;
  livekit_identity: string;
  role: string;
  joined_at: string | null;
  left_at: string | null;
}

export interface ReplayUtteranceDTO {
  id: string;
  participant_id: string;
  participant_identity: string;
  participant_display_name: string;
  text: string;
  is_final: boolean;
  confidence: number | null;
  start_ts: string;
  end_ts: string;
}

export interface ReplayDecisionDTO {
  id: string;
  tick_id: number;
  ts: string;
  action: string;
  target_participant_id: string | null;
  source: string;
  triggering_rule: string | null;
  researcher_id: string | null;
  researcher_hint: string | null;
  reason_codes: string[];
  reason_human: string;
  confidence: number | null;
  suppressed_by: string[];
  was_executed: boolean;
  llm_prompt: Record<string, unknown> | null;
  llm_output: string | null;
  tts_audio_url: string | null;
  spoken_at: string | null;
  cooldown_until: string;
}

export interface ReplaySnapshotDTO {
  id: string;
  tick_id: number;
  ts: string;
  state: unknown;
}

export interface ReplayFlagDTO {
  id: string;
  ts: string;
  researcher_id: string | null;
  note: string | null;
  auto_generated: boolean;
}

export interface ReplaySessionDTO {
  id: string;
  livekit_room_name: string;
  status: string;
  scheduled_start: string | null;
  actual_start: string | null;
  actual_end: string | null;
  created_at: string;
  /**
   * True iff a composite recording R2 key is present on the row.
   * Never the key itself — keys are server-private and only become
   * URLs via `GET /api/sessions/[id]/recordings/audio`.
   */
  has_composite_recording: boolean;
  /**
   * Participant identities that have a per-track recording. Sorted
   * for deterministic rendering. The audio fetch route resolves
   * identity → signed URL on demand.
   */
  participant_recording_identities: string[];
}

/**
 * Page bootstrap payload. The server component computes this once at
 * render time and hands it to the client shell — the shell then makes
 * its own narrower fetches as the researcher scrubs.
 */
export interface ReplayBootstrap {
  session: ReplaySessionDTO;
  participants: ReplayParticipantDTO[];
  decisions: ReplayDecisionDTO[];
  flags: ReplayFlagDTO[];
  initial_utterances: ReplayUtteranceDTO[];
  initial_snapshots: ReplaySnapshotDTO[];
}
