/**
 * AUTO-GENERATED — DO NOT EDIT BY HAND.
 *
 * Source: Pydantic models in services/engine/verbio_engine/domain/.
 * Regenerate via: pnpm shared-types:generate
 */

/* eslint-disable */

export type Id = string;
export type Confidence = number | null;
export type EndTs = string;
export type IsFinal = boolean;
export type ParticipantDisplayName = string;
export type ParticipantId = string;
export type ParticipantIdentity = string;
export type SessionId = string;
export type StartTs = string;
export type Text = string;
export type UtteranceId = string;
export type SessionId1 = string;
export type Ts = string;
export type Type = 'utterance';

/**
 * Envelope published to `verbio:events:{session_id}` for SSE fan-out.
 *
 * Phase 1 always has `type="utterance"`. The literal-narrowed union
 * grows when Phase 3 introduces decision and state-snapshot events;
 * web's discriminated parser then expands accordingly.
 *
 * `id` is the SSE event id — clients echo it back as `Last-Event-ID`
 * on reconnect, and the web SSE route uses it to skip already-seen
 * rows during the Postgres backfill. For utterance events we use the
 * utterance UUID directly; it's globally unique and resolvable to a
 * row for the backfill cursor.
 *
 * `ts` is the server-side moment the event was created (not the
 * utterance's start_ts), used purely for diagnostics — ordering is by
 * payload start_ts on backfill.
 */
export interface TranscriptEvent {
  id: Id;
  payload: UtteranceEventPayload;
  session_id: SessionId1;
  ts: Ts;
  type: Type;
}
/**
 * Snapshot of an utterance row at the moment it was persisted.
 *
 * Self-contained — the dashboard renders without joining back to
 * Postgres. `participant_identity` + `participant_display_name` are
 * denormalised so a single SSE message is enough to draw a row.
 */
export interface UtteranceEventPayload {
  confidence?: Confidence;
  end_ts: EndTs;
  is_final: IsFinal;
  participant_display_name: ParticipantDisplayName;
  participant_id: ParticipantId;
  participant_identity: ParticipantIdentity;
  session_id: SessionId;
  start_ts: StartTs;
  text: Text;
  utterance_id: UtteranceId;
}
