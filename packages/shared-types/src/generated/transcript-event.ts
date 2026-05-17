/**
 * AUTO-GENERATED — DO NOT EDIT BY HAND.
 *
 * Source: Pydantic models in services/engine/verbio_engine/domain/.
 * Regenerate via: pnpm shared-types:generate
 */

/* eslint-disable */

export type TranscriptEvent =
  | UtteranceEventEnvelope
  | StateSnapshotEventEnvelope
  | DecisionEventEnvelope
  | SessionFlagEventEnvelope;
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
export type Id1 = string;
export type SnapshotId = string;
/**
 * Number of participants whose VAD or STT signals speech right now.
 */
export type CurrentlySpeakingCount = number;
/**
 * (t - started_at) in seconds; precomputed for convenience.
 */
export type ElapsedSec = number;
/**
 * Model identifier that produced both embeddings on this snapshot. Persisted into state snapshots so replay can refuse a cross-model comparison.
 */
export type EmbeddingModelName = string | null;
/**
 * Set by `pause_session`; the tick loop still runs but the moderator is suppressed from speaking.
 */
export type IsPaused = boolean;
/**
 * Set by `mute_moderator`; rules still evaluate so the audit trail remains complete.
 */
export type ModeratorMuted = boolean;
export type ActualShareLast5MinPct = number;
export type BackchannelCountLast2Min = number;
/**
 * Human-readable label shown to researchers.
 */
export type DisplayName = string;
export type FairSharePct = number;
/**
 * No backchannels and no speech in the recent window.
 */
export type Disengaged = boolean;
/**
 * Actual share of last 5 min exceeds fair share by configured factor.
 */
export type Dominating = boolean;
/**
 * `was_interrupted_count` exceeds threshold over the rolling window.
 */
export type FrequentlyInterrupted = boolean;
/**
 * Has not spoken in N minutes (configured per study).
 */
export type SilentTooLong = boolean;
/**
 * Times this participant cut someone else off.
 */
export type InterruptionCount = number;
export type IsCurrentlySpeaking = boolean;
export type JoinedAt = string;
export type LastSpokeAt = string | null;
export type LastSpokeDurationSec = number | null;
/**
 * Stable application id.
 */
export type ParticipantId1 = string;
/**
 * @maxItems 5
 */
export type RecentUtterances =
  | []
  | [UtteranceRef]
  | [UtteranceRef, UtteranceRef]
  | [UtteranceRef, UtteranceRef, UtteranceRef]
  | [UtteranceRef, UtteranceRef, UtteranceRef, UtteranceRef]
  | [UtteranceRef, UtteranceRef, UtteranceRef, UtteranceRef, UtteranceRef];
/**
 * Length of the utterance in seconds.
 */
export type DurationSec = number;
/**
 * Wall-clock start time of the utterance.
 */
export type SpokenAt = string;
/**
 * Verbatim transcribed text (may be partial).
 */
export type Text1 = string;
/**
 * Stable id; references utterances.utterance_id.
 */
export type UtteranceId1 = string;
export type RollingTranscript2Min = string;
export type SpeakingTimeLast5MinSec = number;
export type SpeakingTimeLast60Sec = number;
export type SpeakingTimeTotalSec = number;
export type TurnCount = number;
/**
 * Voice activity detected but not yet transcribed.
 */
export type VadActive = boolean;
export type WasInterruptedCount = number;
/**
 * Utterances spoken inside the active rolling window.
 */
export type CurrentWindowCount = number;
export type LastUtteranceAt = string | null;
/**
 * Hard cap on moderator utterances within the rolling 10-minute window.
 */
export type MaxUtterancesPer10Min = number;
/**
 * Floor enforced regardless of remaining window capacity.
 */
export type MinSecondsBetweenUtterances = number;
/**
 * Concatenated finalized text from all speakers in the last 2 min, newest last. Used by `topic_drift` and `stalled_thread`.
 */
export type RollingGlobalTranscript2Min = string;
/**
 * Vector of the last ~30s of group transcript. Refreshed by the state store on each speech-finalization event; lags the raw transcript field by one embed round-trip. None until enough transcript has accumulated to embed.
 */
export type RollingTranscript30SEmbedding = number[] | null;
/**
 * Planned end time; powers `time_remaining_pressure` rule.
 */
export type ScheduledEndAt = string | null;
export type SessionId2 = string;
/**
 * Seconds since the most recent global speech event; `silence_gap` rule reads this directly.
 */
export type SilenceRunSec = number;
/**
 * Wall-clock when the session opened.
 */
export type StartedAt = string;
/**
 * The researcher's framing prompt for this study; embedded once at session load and reused for similarity comparisons.
 */
export type StudyPrompt = string;
/**
 * Vector representation of `study_prompt`. None until the state store has run the embedding call; rules MUST handle the None case (typically: don't fire) so a transient provider outage doesn't fabricate false positives.
 */
export type StudyPromptEmbedding = number[] | null;
/**
 * Wall-clock for the current tick.
 */
export type T = string;
/**
 * Monotonic per-session tick counter. First tick is 0.
 */
export type TickId = number;
export type SessionId3 = string;
export type Ts1 = string;
export type Type1 = 'state_snapshot';
export type Id2 = string;
export type Action =
  | 'stay_silent'
  | 'prompt_participant'
  | 'redirect_topic'
  | 'summarize_thread'
  | 'request_clarification'
  | 'suggest_turn_taking'
  | 'close_session';
export type Confidence1 = number;
export type CooldownUntil = string;
export type DecisionId = string;
export type LlmOutput = string | null;
/**
 * Verbatim mouth-layer input; tightened to a typed shape in Phase 4.
 */
export type LlmPrompt = {
  [k: string]: unknown | undefined;
} | null;
/**
 * Structured codes, e.g. ['silence_gap_8s', 'p3_unheard_4min'].
 */
export type ReasonCodes = string[];
/**
 * Generated single-sentence rationale shown on the dashboard.
 */
export type ReasonHuman = string;
/**
 * Free-text guidance from a manual researcher command.
 */
export type ResearcherHint = string | null;
export type ResearcherId = string | null;
export type SessionId4 = string;
export type Source = 'auto' | 'researcher_manual' | 'researcher_whisper';
export type SpokenAt1 = string | null;
/**
 * ['quietness_budget', 'global_cooldown', 'lower_priority_won', ...].
 */
export type SuppressedBy = string[];
export type TargetParticipantId = string | null;
/**
 * Monotonic per-session tick counter.
 */
export type TickId1 = number;
export type Timestamp = string;
/**
 * Rule name when source='auto'; null otherwise.
 */
export type TriggeringRule = string | null;
export type TtsAudioUrl = string | null;
export type WasExecuted = boolean;
export type SessionId5 = string;
export type Ts2 = string;
export type Type2 = 'decision';
export type Id3 = string;
export type AutoGenerated = boolean;
export type FlagId = string;
export type Note = string | null;
export type ResearcherId1 = string | null;
export type SessionId6 = string;
export type Ts3 = string;
export type SessionId7 = string;
export type Ts4 = string;
export type Type3 = 'session_flag';

/**
 * SSE envelope for an STT-derived utterance.
 *
 * `id` is the utterance UUID as a string — SSE clients echo it back as
 * `Last-Event-ID` on reconnect, and the web SSE route uses it to skip
 * already-seen rows during the Postgres backfill.
 *
 * `ts` is the server-side moment the event was created (not the
 * utterance's start_ts), used purely for diagnostics — ordering is by
 * payload start_ts on backfill.
 */
export interface UtteranceEventEnvelope {
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
/**
 * SSE envelope for one per-tick SessionState snapshot.
 *
 * `id` is the snapshot row UUID as a string; `ts` is the wall-clock
 * moment the tick projected the snapshot (mirrors `state.t`). The
 * Last-Event-ID cursor uses `id` to backfill from
 * `state_snapshots.id > {last_id}` on reconnect — the table's
 * (session_id, tick_id) index covers that scan cheaply.
 */
export interface StateSnapshotEventEnvelope {
  id: Id1;
  payload: StateSnapshotEventPayload;
  session_id: SessionId3;
  ts: Ts1;
  type: Type1;
}
/**
 * Snapshot of one full `SessionState` row at the moment it was persisted.
 *
 * Carries the entire SessionState so the dashboard can render every
 * tile (speaking time, last spoke, currently_speaking, silence_run,
 * flags) from a single message. `snapshot_id` is the Postgres row id;
 * keeping it here as well as on the envelope mirrors the utterance
 * variant and gives the web side typed-UUID access without re-parsing
 * the envelope id.
 */
export interface StateSnapshotEventPayload {
  snapshot_id: SnapshotId;
  state: SessionState;
}
/**
 * Per-tick projection of everything a rule needs to read.
 */
export interface SessionState {
  currently_speaking_count?: CurrentlySpeakingCount;
  elapsed_sec: ElapsedSec;
  embedding_model_name?: EmbeddingModelName;
  is_paused?: IsPaused;
  moderator_muted?: ModeratorMuted;
  participants?: Participants;
  quietness_budget?: QuietnessBudget;
  rolling_global_transcript_2min?: RollingGlobalTranscript2Min;
  rolling_transcript_30s_embedding?: RollingTranscript30SEmbedding;
  scheduled_end_at?: ScheduledEndAt;
  session_id: SessionId2;
  silence_run_sec?: SilenceRunSec;
  started_at: StartedAt;
  study_prompt?: StudyPrompt;
  study_prompt_embedding?: StudyPromptEmbedding;
  t: T;
  tick_id: TickId;
}
/**
 * Keyed by `participant_id`. Only currently-joined participants.
 */
export interface Participants {
  [k: string]: ParticipantState | undefined;
}
/**
 * Per-participant snapshot consumed by every rule on every tick.
 */
export interface ParticipantState {
  actual_share_last_5min_pct?: ActualShareLast5MinPct;
  backchannel_count_last_2min?: BackchannelCountLast2Min;
  display_name: DisplayName;
  fair_share_pct?: FairSharePct;
  flags?: ParticipantFlags;
  interruption_count?: InterruptionCount;
  is_currently_speaking?: IsCurrentlySpeaking;
  joined_at: JoinedAt;
  last_spoke_at?: LastSpokeAt;
  last_spoke_duration_sec?: LastSpokeDurationSec;
  participant_id: ParticipantId1;
  recent_utterances?: RecentUtterances;
  rolling_transcript_2min?: RollingTranscript2Min;
  speaking_time_last_5min_sec?: SpeakingTimeLast5MinSec;
  speaking_time_last_60sec?: SpeakingTimeLast60Sec;
  speaking_time_total_sec?: SpeakingTimeTotalSec;
  turn_count?: TurnCount;
  vad_active?: VadActive;
  was_interrupted_count?: WasInterruptedCount;
}
/**
 * Derived booleans recomputed each tick from rolling state.
 *
 * Predicates are documented in `docs/rules-reference.md`. Each rule's
 * predicate may consume one or more of these flags.
 */
export interface ParticipantFlags {
  disengaged?: Disengaged;
  dominating?: Dominating;
  frequently_interrupted?: FrequentlyInterrupted;
  silent_too_long?: SilentTooLong;
}
/**
 * A pointer to a recent utterance with the timing metadata the rules need.
 */
export interface UtteranceRef {
  duration_sec: DurationSec;
  spoken_at: SpokenAt;
  text: Text1;
  utterance_id: UtteranceId1;
}
/**
 * Live-adjustable rate limit (brief §7.4).
 */
export interface QuietnessBudget {
  current_window_count?: CurrentWindowCount;
  last_utterance_at?: LastUtteranceAt;
  max_utterances_per_10min?: MaxUtterancesPer10Min;
  min_seconds_between_utterances?: MinSecondsBetweenUtterances;
}
/**
 * SSE envelope for one per-tick moderator decision.
 *
 * `id` mirrors the `decisions.id` UUID as a string so the web SSE
 * route's Last-Event-ID backfill maps it straight to the row. `ts`
 * is the tick wall-clock (`decision.timestamp`) so the client can
 * order across reconnects without unwrapping the payload.
 */
export interface DecisionEventEnvelope {
  id: Id2;
  payload: DecisionEventPayload;
  session_id: SessionId5;
  ts: Ts2;
  type: Type2;
}
/**
 * Snapshot of one moderator decision at the moment it was persisted.
 *
 * Carries the full `ModeratorDecision` plus the DB row id (`decision_id`
 * matches `decisions.id`). The dashboard's decision log + "Why quiet
 * now?" panel render from this payload alone — no second trip to
 * Postgres for the common case.
 *
 * In shadow mode (Phase 3) `was_executed` is always False and the
 * mouth-layer fields (`llm_prompt`, `llm_output`, `tts_audio_url`,
 * `spoken_at`) are all None. The envelope ships anyway so the
 * dashboard can show the silent-decision stream while researchers
 * review.
 */
export interface DecisionEventPayload {
  decision: ModeratorDecision;
}
/**
 * Single decision record from one tick of the engine.
 *
 * Invariants enforced elsewhere (`verbio_engine.tick_loop`):
 *   * `was_executed` is True only for non-silent actions that completed
 *     before the latency budget elapsed.
 *   * `cooldown_until` is computed from the triggering rule's
 *     `default_cooldown_sec`; researcher overrides may extend it.
 */
export interface ModeratorDecision {
  action: Action;
  confidence?: Confidence1;
  cooldown_until: CooldownUntil;
  decision_id: DecisionId;
  llm_output?: LlmOutput;
  llm_prompt?: LlmPrompt;
  reason_codes?: ReasonCodes;
  reason_human?: ReasonHuman;
  researcher_hint?: ResearcherHint;
  researcher_id?: ResearcherId;
  session_id: SessionId4;
  source: Source;
  spoken_at?: SpokenAt1;
  suppressed_by?: SuppressedBy;
  target_participant_id?: TargetParticipantId;
  tick_id: TickId1;
  timestamp: Timestamp;
  triggering_rule?: TriggeringRule;
  tts_audio_url?: TtsAudioUrl;
  was_executed?: WasExecuted;
}
/**
 * SSE envelope for one session-flag bookmark.
 *
 * `id` mirrors the `session_flags.id` UUID as a string — which is the
 * `command_id` for researcher-issued flags, so a client that already
 * saw the `researcher_action` SSE for the same `flag_moment` can
 * deduplicate against the audit message it already rendered.
 *
 * `ts` is the bookmark's wall-clock (the researcher's click time),
 * matching `payload.ts` — clients order the flag rail by `ts` without
 * unwrapping the payload.
 */
export interface SessionFlagEventEnvelope {
  id: Id3;
  payload: SessionFlagEventPayload;
  session_id: SessionId7;
  ts: Ts4;
  type: Type3;
}
/**
 * Snapshot of one session_flag row at the moment it was persisted.
 *
 * Carries the bookmark fields the dashboard's flag rail renders
 * inline — `note` is shown alongside the timestamp; `researcher_id`
 * distinguishes flags from different researchers in a multi-observer
 * session; `auto_generated` lets the UI render engine-detected vs.
 * human-issued bookmarks differently (future, P5 L7 only writes
 * researcher-issued).
 */
export interface SessionFlagEventPayload {
  auto_generated: AutoGenerated;
  flag_id: FlagId;
  note: Note;
  researcher_id: ResearcherId1;
  session_id: SessionId6;
  ts: Ts3;
}
