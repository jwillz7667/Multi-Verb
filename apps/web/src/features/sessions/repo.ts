/**
 * ModeratedSession repository — Prisma-backed reads + writes.
 *
 * Keeps Prisma calls out of route handlers and server actions: tests
 * can replace the repo with an in-memory double, and the call sites
 * stay focused on flow control rather than ORM mechanics.
 *
 * Tenancy: in later phases (org_id) this repo gains a scoped variant.
 * For Phase 1 there are no orgs yet — sessions are global.
 */

import 'server-only';

import { randomUUID } from 'node:crypto';

import { db } from '@/lib/db';

import type { CreatedSession, SessionStatus } from './types';

export interface ModeratedSessionRow {
  id: string;
  livekitRoomName: string;
  status: SessionStatus;
  scheduledStart: Date | null;
  actualStart: Date | null;
  actualEnd: Date | null;
  createdAt: Date;
}

interface CreateInput {
  scheduledStart: Date | null;
}

/**
 * Insert a fresh `sessions` row. The agent worker resolves which
 * session to attach to by `livekit_room_name`, so we mint a
 * cryptographically random room name (URL-safe + collision-resistant
 * within the unique constraint).
 */
export async function createSession(input: CreateInput): Promise<CreatedSession> {
  // `room-<8-char hex>` is short enough for invite URLs and
  // collision-safe at our session volume (2^32 names; uq constraint
  // catches the astronomically unlikely collision).
  const roomName = `room-${randomUUID().replace(/-/g, '').slice(0, 16)}`;
  const row = await db.moderatedSession.create({
    data: {
      livekitRoomName: roomName,
      status: 'scheduled',
      scheduledStart: input.scheduledStart,
    },
    select: {
      id: true,
      livekitRoomName: true,
      status: true,
      scheduledStart: true,
      createdAt: true,
    },
  });
  return {
    id: row.id,
    livekitRoomName: row.livekitRoomName,
    status: row.status as SessionStatus,
    scheduledStart: row.scheduledStart,
    createdAt: row.createdAt,
  };
}

export async function findSessionById(id: string): Promise<ModeratedSessionRow | null> {
  const row = await db.moderatedSession.findUnique({
    where: { id },
    select: {
      id: true,
      livekitRoomName: true,
      status: true,
      scheduledStart: true,
      actualStart: true,
      actualEnd: true,
      createdAt: true,
    },
  });
  if (row === null) return null;
  return {
    id: row.id,
    livekitRoomName: row.livekitRoomName,
    status: row.status as SessionStatus,
    scheduledStart: row.scheduledStart,
    actualStart: row.actualStart,
    actualEnd: row.actualEnd,
    createdAt: row.createdAt,
  };
}

/**
 * Replay-mode hydration row.
 *
 * Carries every field the `/sessions/[id]/replay` page needs to bootstrap:
 *   - lifecycle bounds (`actualStart` / `actualEnd`) for the timeline scale,
 *   - LiveKit room name for the breadcrumb,
 *   - the composite recording R2 key + the per-participant key map so the
 *     audio route can mint signed URLs without a second SELECT,
 *   - `configSnapshot` so the replay can render the rules version + persona
 *     the session ran under (frozen at start time per brief §7.5).
 */
export interface ReplaySessionRow extends ModeratedSessionRow {
  recordingUrl: string | null;
  // JSONB: written by the webhook handler as `{ identity: r2Key }`. The
  // shape is validated downstream in `features/recordings/replay-urls`
  // before being handed to the UI.
  perParticipantRecordingUrls: unknown;
  configSnapshot: unknown;
}

/**
 * Load a session row with everything the replay page bootstraps from.
 *
 * The live dashboard's `findSessionById` deliberately skips the JSONB
 * columns (configSnapshot, perParticipantRecordingUrls) because the
 * live tiles don't read them and a hot reload of the session detail
 * page shouldn't pull megabytes of state per render. Replay reads
 * them once on mount and stops, so the heavier projection is fine.
 *
 * Returns `null` for unknown ids — the page handler turns that into a
 * 404 so the response can't discriminate between "doesn't exist" and
 * "exists in another org" once tenancy lands.
 */
export async function findSessionForReplay(id: string): Promise<ReplaySessionRow | null> {
  const row = await db.moderatedSession.findUnique({
    where: { id },
    select: {
      id: true,
      livekitRoomName: true,
      status: true,
      scheduledStart: true,
      actualStart: true,
      actualEnd: true,
      createdAt: true,
      recordingUrl: true,
      perParticipantRecordingUrls: true,
      configSnapshot: true,
    },
  });
  if (row === null) return null;
  return {
    id: row.id,
    livekitRoomName: row.livekitRoomName,
    status: row.status as SessionStatus,
    scheduledStart: row.scheduledStart,
    actualStart: row.actualStart,
    actualEnd: row.actualEnd,
    createdAt: row.createdAt,
    recordingUrl: row.recordingUrl,
    perParticipantRecordingUrls: row.perParticipantRecordingUrls,
    configSnapshot: row.configSnapshot,
  };
}

export interface ParticipantRow {
  id: string;
  sessionId: string;
  displayName: string;
  role: string;
  joinedAt: Date | null;
  leftAt: Date | null;
  livekitIdentity: string;
}

/**
 * Roster for a session — used to render the 5 stacked speech bands and
 * the participant filter dropdown in the replay UI.
 *
 * Ordered by `joinedAt ASC NULLS LAST` so the band order on the
 * timeline matches the joining order (deterministic across re-renders).
 * Participants who never connected sit at the bottom — they have no
 * utterances to render but are still listed for completeness.
 */
export async function listParticipantsForSession(sessionId: string): Promise<ParticipantRow[]> {
  const rows = await db.participant.findMany({
    where: { sessionId },
    orderBy: [{ joinedAt: { sort: 'asc', nulls: 'last' } }, { id: 'asc' }],
    select: {
      id: true,
      sessionId: true,
      displayName: true,
      role: true,
      joinedAt: true,
      leftAt: true,
      livekitIdentity: true,
    },
  });
  return rows.map((row) => ({
    id: row.id,
    sessionId: row.sessionId,
    displayName: row.displayName,
    role: row.role,
    joinedAt: row.joinedAt,
    leftAt: row.leftAt,
    livekitIdentity: row.livekitIdentity,
  }));
}

export async function listRecentSessions(limit = 25): Promise<ModeratedSessionRow[]> {
  const rows = await db.moderatedSession.findMany({
    orderBy: { createdAt: 'desc' },
    take: limit,
    select: {
      id: true,
      livekitRoomName: true,
      status: true,
      scheduledStart: true,
      actualStart: true,
      actualEnd: true,
      createdAt: true,
    },
  });
  return rows.map((row) => ({
    id: row.id,
    livekitRoomName: row.livekitRoomName,
    status: row.status as SessionStatus,
    scheduledStart: row.scheduledStart,
    actualStart: row.actualStart,
    actualEnd: row.actualEnd,
    createdAt: row.createdAt,
  }));
}

export interface UtteranceWithSpeakerRow {
  id: string;
  sessionId: string;
  participantId: string;
  participantIdentity: string;
  participantDisplayName: string;
  text: string;
  isFinal: boolean;
  confidence: number | null;
  startTs: Date;
  endTs: Date;
}

interface BackfillOptions {
  afterUtteranceId?: string;
  limit?: number;
}

/**
 * Backfill utterances since the SSE `Last-Event-ID` cursor.
 *
 * Phase 1's SSE event id IS the utterance UUID. On reconnect the
 * client echoes it back as `Last-Event-ID`; we resolve it to its
 * `start_ts` and return everything ordered after it, so the browser
 * can resume without gaps before we re-subscribe to Redis pub/sub.
 *
 * `(start_ts, id)` is the stable replay key — start_ts may tie
 * across concurrent participants, so we use id as the tiebreaker.
 * The query hits the `ix_utterances_session_id_start_ts` index.
 *
 * When `afterUtteranceId` is unknown (first connect, or the cursor
 * row has been pruned), we return the full session transcript bounded
 * by `limit`. Default cap is 500 rows — large enough to cover a
 * brief disconnect mid-session, small enough that a misbehaving
 * client can't sweep an entire 60-min session in one request.
 */
export interface StateSnapshotRow {
  id: string;
  sessionId: string;
  tickId: bigint;
  ts: Date;
  state: unknown;
}

interface SnapshotBackfillOptions {
  afterSnapshotId?: string;
  limit?: number;
}

/**
 * Backfill `state_snapshots` since the SSE `Last-Event-ID` cursor.
 *
 * The engine writes one snapshot per tick (2 Hz → 7200 rows/hour); a
 * brief disconnect can leave the dashboard several ticks behind. On
 * reconnect the client sends the last snapshot envelope id as
 * `Last-Event-ID`; we resolve it to its `(tickId, id)` cursor and
 * return every snapshot that followed in stable order.
 *
 * `(tick_id, id)` is the replay key. Ties on tick_id (rare — the
 * engine is single-writer per session) are broken by row id for a
 * deterministic order matching the live publish order. The query is
 * served by `ix_state_snapshots_session_id_tick_id`.
 *
 * `limit` defaults to 240 — 2 minutes of ticks at 2 Hz. That's enough
 * to cover a tab-resume after a short suspend without flooding the
 * dashboard with stale frames on long disconnects (clients re-paint
 * from the freshest snapshot anyway). Caller can override for testing.
 */
export async function listStateSnapshotsSince(
  sessionId: string,
  options: SnapshotBackfillOptions = {},
): Promise<StateSnapshotRow[]> {
  const limit = options.limit ?? 240;

  let cursorTickId: bigint | null = null;
  let cursorId: string | null = null;
  if (options.afterSnapshotId !== undefined) {
    const cursor = await db.stateSnapshot.findUnique({
      where: { id: options.afterSnapshotId },
      select: { id: true, tickId: true, sessionId: true },
    });
    // Cross-session cursor smuggling: silently drop, never bleed rows.
    if (cursor !== null && cursor.sessionId === sessionId) {
      cursorTickId = cursor.tickId;
      cursorId = cursor.id;
    }
  }

  const rows = await db.stateSnapshot.findMany({
    where: {
      sessionId,
      ...(cursorTickId !== null && cursorId !== null
        ? {
            OR: [
              { tickId: { gt: cursorTickId } },
              { AND: [{ tickId: cursorTickId }, { id: { gt: cursorId } }] },
            ],
          }
        : {}),
    },
    orderBy: [{ tickId: 'asc' }, { id: 'asc' }],
    take: limit,
    select: {
      id: true,
      sessionId: true,
      tickId: true,
      ts: true,
      state: true,
    },
  });

  return rows.map((row) => ({
    id: row.id,
    sessionId: row.sessionId,
    tickId: row.tickId,
    ts: row.ts,
    state: row.state,
  }));
}

// ---------------------------------------------------------------------------
// Decisions — moderator audit log (brief §10.1, Phase 3 L12).
// ---------------------------------------------------------------------------

export interface DecisionRow {
  id: string;
  sessionId: string;
  tickId: bigint;
  ts: Date;
  action: string;
  targetParticipantId: string | null;
  source: string;
  triggeringRule: string | null;
  researcherId: string | null;
  researcherHint: string | null;
  reasonCodes: string[];
  reasonHuman: string;
  confidence: number | null;
  suppressedBy: string[];
  wasExecuted: boolean;
  // JSONB column. The engine only ever writes a top-level object (the
  // serialised prompt + decision context), so the row type is narrowed
  // to `Record<string, unknown> | null` rather than the broader
  // `Prisma.JsonValue` — callers shape it directly into the wire envelope.
  llmPrompt: Record<string, unknown> | null;
  llmOutput: string | null;
  ttsAudioUrl: string | null;
  spokenAt: Date | null;
  cooldownUntil: Date;
}

interface DecisionBackfillOptions {
  afterDecisionId?: string;
  limit?: number;
}

/**
 * Backfill `decisions` since the SSE `Last-Event-ID` cursor.
 *
 * The engine writes one decision per tick (shadow mode: every tick is
 * `stay_silent` with the firing-rule audit trail attached). On
 * reconnect the dashboard echoes the last decision envelope id; we
 * resolve it to its `(ts, id)` cursor and return everything that
 * followed in stable order. The `(session_id, ts)` index covers it
 * cheaply.
 *
 * Default `limit` is 240 rows — 2 minutes of decisions at 2 Hz, in
 * line with the snapshot backfill cap. The dashboard's decision log
 * pages older rows on demand.
 */
export async function listDecisionsSince(
  sessionId: string,
  options: DecisionBackfillOptions = {},
): Promise<DecisionRow[]> {
  const limit = options.limit ?? 240;

  let cursorTs: Date | null = null;
  let cursorId: string | null = null;
  if (options.afterDecisionId !== undefined) {
    const cursor = await db.decision.findUnique({
      where: { id: options.afterDecisionId },
      select: { id: true, ts: true, sessionId: true },
    });
    // Cross-session smuggling guard — mirrors the utterance + snapshot paths.
    if (cursor !== null && cursor.sessionId === sessionId) {
      cursorTs = cursor.ts;
      cursorId = cursor.id;
    }
  }

  const rows = await db.decision.findMany({
    where: {
      sessionId,
      ...(cursorTs !== null && cursorId !== null
        ? {
            OR: [{ ts: { gt: cursorTs } }, { AND: [{ ts: cursorTs }, { id: { gt: cursorId } }] }],
          }
        : {}),
    },
    orderBy: [{ ts: 'asc' }, { id: 'asc' }],
    take: limit,
  });

  return rows.map((row) => ({
    id: row.id,
    sessionId: row.sessionId,
    tickId: row.tickId,
    ts: row.ts,
    action: row.action,
    targetParticipantId: row.targetParticipantId,
    source: row.source,
    triggeringRule: row.triggeringRule,
    researcherId: row.researcherId,
    researcherHint: row.researcherHint,
    reasonCodes: row.reasonCodes,
    reasonHuman: row.reasonHuman,
    confidence: row.confidence,
    suppressedBy: row.suppressedBy,
    wasExecuted: row.wasExecuted,
    // Prisma's JsonValue is broader than what the engine actually writes
    // (top-level object only). Narrow here at the persistence boundary
    // so downstream consumers see the canonical wire shape.
    llmPrompt: row.llmPrompt as Record<string, unknown> | null,
    llmOutput: row.llmOutput,
    ttsAudioUrl: row.ttsAudioUrl,
    spokenAt: row.spokenAt,
    cooldownUntil: row.cooldownUntil,
  }));
}

export interface RuleEvaluationRow {
  id: string;
  decisionId: string;
  ruleName: string;
  ruleVersion: string;
  fired: boolean;
  suppressedReason: string | null;
  predicateInputs: unknown;
  confidence: number;
}

/**
 * Resolve a decision id to its owning session id.
 *
 * The "Why quiet now?" endpoint needs this to enforce session-scoped
 * access before exposing a decision's `rule_evaluations`: a crafted
 * request must never reveal evaluations from a different session.
 * Returns null when the decision is unknown.
 */
export async function findDecisionSessionId(decisionId: string): Promise<string | null> {
  const row = await db.decision.findUnique({
    where: { id: decisionId },
    select: { sessionId: true },
  });
  return row?.sessionId ?? null;
}

/**
 * Fetch every `rule_evaluations` row for a given decision.
 *
 * The "Why quiet now?" panel renders the per-rule verdict (fired vs.
 * suppressed + reason) for the most recent decision. Decision events
 * on the wire don't carry evaluations (too verbose for every tick at
 * 2 Hz); the panel pulls them on demand when the user opens the
 * inspector for a row.
 */
export async function listRuleEvaluationsForDecision(
  decisionId: string,
): Promise<RuleEvaluationRow[]> {
  const rows = await db.ruleEvaluation.findMany({
    where: { decisionId },
    orderBy: [{ ruleName: 'asc' }],
  });
  return rows.map((row) => ({
    id: row.id,
    decisionId: row.decisionId,
    ruleName: row.ruleName,
    ruleVersion: row.ruleVersion,
    fired: row.fired,
    suppressedReason: row.suppressedReason,
    predicateInputs: row.predicateInputs,
    confidence: row.confidence,
  }));
}

export async function listUtterancesSince(
  sessionId: string,
  options: BackfillOptions = {},
): Promise<UtteranceWithSpeakerRow[]> {
  const limit = options.limit ?? 500;

  let cursorStartTs: Date | null = null;
  let cursorId: string | null = null;
  if (options.afterUtteranceId !== undefined) {
    const cursor = await db.utterance.findUnique({
      where: { id: options.afterUtteranceId },
      select: { id: true, startTs: true, sessionId: true },
    });
    // Silently ignore cursors that don't belong to this session — a
    // crafted Last-Event-ID must never bleed rows across sessions.
    if (cursor !== null && cursor.sessionId === sessionId) {
      cursorStartTs = cursor.startTs;
      cursorId = cursor.id;
    }
  }

  const rows = await db.utterance.findMany({
    where: {
      sessionId,
      ...(cursorStartTs !== null && cursorId !== null
        ? {
            OR: [
              { startTs: { gt: cursorStartTs } },
              { AND: [{ startTs: cursorStartTs }, { id: { gt: cursorId } }] },
            ],
          }
        : {}),
    },
    orderBy: [{ startTs: 'asc' }, { id: 'asc' }],
    take: limit,
    select: {
      id: true,
      sessionId: true,
      participantId: true,
      text: true,
      isFinal: true,
      confidence: true,
      startTs: true,
      endTs: true,
      participant: {
        select: {
          livekitIdentity: true,
          displayName: true,
        },
      },
    },
  });

  return rows.map((row) => ({
    id: row.id,
    sessionId: row.sessionId,
    participantId: row.participantId,
    participantIdentity: row.participant.livekitIdentity,
    participantDisplayName: row.participant.displayName,
    text: row.text,
    isFinal: row.isFinal,
    confidence: row.confidence,
    startTs: row.startTs,
    endTs: row.endTs,
  }));
}

// ---------------------------------------------------------------------------
// Range queries — replay-mode reads (brief §11.2, Phase 6 L4).
//
// The live dashboard pages off `*Since` cursor queries because it only
// ever moves forward through time. Replay scrubs both directions over
// a bounded window, so we expose simple inclusive `[fromTs, toTs]`
// range queries against the same `(session_id, ts)` indexes.
//
// All four functions:
//   - clamp `limit` server-side so a hand-crafted query can't request
//     a session's worth of rows in one shot,
//   - default `fromTs` / `toTs` to "no bound" so the page bootstrap
//     can ask for the full session in one call when small (flags,
//     participants), and chunk when large (utterances, snapshots).
//
// Tenancy is enforced by the route's `findSessionById` lookup before
// these are called — there's no separate org check here because the
// session row IS the tenancy boundary (no cross-session leakage at the
// query level).
// ---------------------------------------------------------------------------

interface RangeQueryOptions {
  fromTs?: Date;
  toTs?: Date;
  limit?: number;
}

const UTTERANCE_RANGE_MAX = 10_000;
const DECISION_RANGE_MAX = 5_000;
const SNAPSHOT_RANGE_MAX = 1_200; // 10 min @ 2 Hz — replay rarely needs more in one chunk.
const FLAG_RANGE_MAX = 500;

function clampLimit(requested: number | undefined, fallback: number, max: number): number {
  if (requested === undefined) return fallback;
  if (requested <= 0) return fallback;
  return Math.min(requested, max);
}

function tsFilter(fromTs: Date | undefined, toTs: Date | undefined): { gte?: Date; lte?: Date } {
  const filter: { gte?: Date; lte?: Date } = {};
  if (fromTs !== undefined) filter.gte = fromTs;
  if (toTs !== undefined) filter.lte = toTs;
  return filter;
}

/**
 * Range-fetch utterances by `start_ts ∈ [fromTs, toTs]`.
 *
 * Used by the replay transcript panel and the per-participant speech
 * band renderer. Returns the same `UtteranceWithSpeakerRow` shape as
 * the live SSE backfill so the renderer logic can be shared.
 *
 * Default `limit` is the chunk size the page loads on first paint
 * (5,000 rows — enough for ~5 min of dense five-way conversation).
 * Hard cap at 10,000 to keep a single request under the Vercel
 * function memory envelope.
 */
export async function listUtterancesByRange(
  sessionId: string,
  options: RangeQueryOptions = {},
): Promise<UtteranceWithSpeakerRow[]> {
  const limit = clampLimit(options.limit, 5_000, UTTERANCE_RANGE_MAX);
  const startTs = tsFilter(options.fromTs, options.toTs);
  const rows = await db.utterance.findMany({
    where: {
      sessionId,
      ...(startTs.gte !== undefined || startTs.lte !== undefined ? { startTs } : {}),
    },
    orderBy: [{ startTs: 'asc' }, { id: 'asc' }],
    take: limit,
    select: {
      id: true,
      sessionId: true,
      participantId: true,
      text: true,
      isFinal: true,
      confidence: true,
      startTs: true,
      endTs: true,
      participant: {
        select: {
          livekitIdentity: true,
          displayName: true,
        },
      },
    },
  });
  return rows.map((row) => ({
    id: row.id,
    sessionId: row.sessionId,
    participantId: row.participantId,
    participantIdentity: row.participant.livekitIdentity,
    participantDisplayName: row.participant.displayName,
    text: row.text,
    isFinal: row.isFinal,
    confidence: row.confidence,
    startTs: row.startTs,
    endTs: row.endTs,
  }));
}

/**
 * Range-fetch decisions by `ts ∈ [fromTs, toTs]`.
 *
 * Used by the replay timeline (color-by-action markers) and the
 * decision detail panel. Returns the full `DecisionRow` projection
 * including the LLM prompt + output and reason codes — the panel
 * renders them all when a decision is selected, and re-fetching on
 * click would add a request-per-marker round-trip nobody wants.
 *
 * Default `limit` is 2,000 — a 60-min session at 2 Hz produces 7,200
 * decisions but the vast majority are `stay_silent`. A real replay
 * filters down to executed decisions client-side; the server returns
 * everything in window so the audit trail is complete (brief §2.2).
 */
export async function listDecisionsByRange(
  sessionId: string,
  options: RangeQueryOptions = {},
): Promise<DecisionRow[]> {
  const limit = clampLimit(options.limit, 2_000, DECISION_RANGE_MAX);
  const ts = tsFilter(options.fromTs, options.toTs);
  const rows = await db.decision.findMany({
    where: {
      sessionId,
      ...(ts.gte !== undefined || ts.lte !== undefined ? { ts } : {}),
    },
    orderBy: [{ ts: 'asc' }, { id: 'asc' }],
    take: limit,
  });
  return rows.map((row) => ({
    id: row.id,
    sessionId: row.sessionId,
    tickId: row.tickId,
    ts: row.ts,
    action: row.action,
    targetParticipantId: row.targetParticipantId,
    source: row.source,
    triggeringRule: row.triggeringRule,
    researcherId: row.researcherId,
    researcherHint: row.researcherHint,
    reasonCodes: row.reasonCodes,
    reasonHuman: row.reasonHuman,
    confidence: row.confidence,
    suppressedBy: row.suppressedBy,
    wasExecuted: row.wasExecuted,
    llmPrompt: row.llmPrompt as Record<string, unknown> | null,
    llmOutput: row.llmOutput,
    ttsAudioUrl: row.ttsAudioUrl,
    spokenAt: row.spokenAt,
    cooldownUntil: row.cooldownUntil,
  }));
}

/**
 * Stream every final utterance for a session into an async iterator.
 *
 * Exports (transcript .txt / .vtt — and the L11 decision CSV builds
 * on the same pattern) need every row, not the bootstrap window the
 * replay UI loads. Pulling 10k+ rows in a single SELECT is fine
 * latency-wise but blows the Vercel function memory envelope on a
 * dense 90-minute session. Cursor pagination over `(start_ts, id)`
 * keeps memory bounded to one chunk at a time.
 *
 * Returns a `null`-on-exhaustion async iterator so callers can
 * `for await (const chunk of …)` and pipe directly into a streaming
 * writer.
 */
export async function* iterateAllUtterancesForSession(
  sessionId: string,
  chunkSize = 2_000,
): AsyncGenerator<UtteranceWithSpeakerRow[], void, void> {
  let cursorStartTs: Date | undefined;
  let cursorId: string | undefined;
  let hasMore = true;
  while (hasMore) {
    const rows = await db.utterance.findMany({
      where: {
        sessionId,
        isFinal: true,
        ...(cursorStartTs !== undefined && cursorId !== undefined
          ? {
              OR: [
                { startTs: { gt: cursorStartTs } },
                { startTs: cursorStartTs, id: { gt: cursorId } },
              ],
            }
          : {}),
      },
      orderBy: [{ startTs: 'asc' }, { id: 'asc' }],
      take: chunkSize,
      select: {
        id: true,
        sessionId: true,
        participantId: true,
        text: true,
        isFinal: true,
        confidence: true,
        startTs: true,
        endTs: true,
        participant: {
          select: {
            livekitIdentity: true,
            displayName: true,
          },
        },
      },
    });
    if (rows.length === 0) return;
    yield rows.map((row) => ({
      id: row.id,
      sessionId: row.sessionId,
      participantId: row.participantId,
      participantIdentity: row.participant.livekitIdentity,
      participantDisplayName: row.participant.displayName,
      text: row.text,
      isFinal: row.isFinal,
      confidence: row.confidence,
      startTs: row.startTs,
      endTs: row.endTs,
    }));
    const last = rows[rows.length - 1];
    if (last === undefined) return;
    cursorStartTs = last.startTs;
    cursorId = last.id;
    // A short page means the page tail is the table tail — no need to
    // round-trip again just to discover an empty result.
    hasMore = rows.length === chunkSize;
  }
}

/**
 * Fetch only decisions that actually went through the mouth — i.e.,
 * the moderator's spoken turns. The brief persists every tick (≈ 7200
 * rows per hour at 2 Hz) but the transcript export only wants the few
 * dozen rows that produced audio. Filtering at the DB level skips a
 * giant read for a tiny result set.
 *
 * Ordered by `(spoken_at, id)` where present, falling back to `ts`
 * for rows that lack a `spoken_at` (transitional state at the moment
 * of execution). The export merges these with utterances, sorting by
 * the line's start time, so the table-level ordering here is mostly a
 * cosmetic safety net for deterministic output.
 */
export async function listExecutedModeratorTurns(sessionId: string): Promise<DecisionRow[]> {
  const rows = await db.decision.findMany({
    where: {
      sessionId,
      wasExecuted: true,
      NOT: { llmOutput: null },
    },
    orderBy: [{ ts: 'asc' }, { id: 'asc' }],
  });
  return rows.map((row) => ({
    id: row.id,
    sessionId: row.sessionId,
    tickId: row.tickId,
    ts: row.ts,
    action: row.action,
    targetParticipantId: row.targetParticipantId,
    source: row.source,
    triggeringRule: row.triggeringRule,
    researcherId: row.researcherId,
    researcherHint: row.researcherHint,
    reasonCodes: row.reasonCodes,
    reasonHuman: row.reasonHuman,
    confidence: row.confidence,
    suppressedBy: row.suppressedBy,
    wasExecuted: row.wasExecuted,
    llmPrompt: row.llmPrompt as Record<string, unknown> | null,
    llmOutput: row.llmOutput,
    ttsAudioUrl: row.ttsAudioUrl,
    spokenAt: row.spokenAt,
    cooldownUntil: row.cooldownUntil,
  }));
}

/**
 * Range-fetch state snapshots by `ts ∈ [fromTs, toTs]`.
 *
 * Used by the replay state snapshot panel. Snapshots write at 2 Hz
 * (one row per tick), so a 60-min session is 7,200 rows. The replay
 * UI loads narrow windows around the scrubber position — default
 * `limit` is 600 (5 minutes of context) and hard cap is 1,200 (10
 * minutes) to keep the JSON payload below ~1 MB per request.
 *
 * Returns `state` as `unknown`; the state-panel component validates
 * via the same Zod schema the SSE backfill uses, so a corrupt
 * snapshot is dropped rather than crashing the panel.
 */
export async function listSnapshotsByRange(
  sessionId: string,
  options: RangeQueryOptions = {},
): Promise<StateSnapshotRow[]> {
  const limit = clampLimit(options.limit, 600, SNAPSHOT_RANGE_MAX);
  const ts = tsFilter(options.fromTs, options.toTs);
  const rows = await db.stateSnapshot.findMany({
    where: {
      sessionId,
      ...(ts.gte !== undefined || ts.lte !== undefined ? { ts } : {}),
    },
    orderBy: [{ ts: 'asc' }, { id: 'asc' }],
    take: limit,
    select: {
      id: true,
      sessionId: true,
      tickId: true,
      ts: true,
      state: true,
    },
  });
  return rows.map((row) => ({
    id: row.id,
    sessionId: row.sessionId,
    tickId: row.tickId,
    ts: row.ts,
    state: row.state,
  }));
}

/**
 * Find the freshest snapshot at or before a given timestamp.
 *
 * Used by the replay state panel when the scrubber moves outside the
 * 5-minute bootstrap window — instead of round-tripping a wide range
 * query just to throw most of it away, we fetch one row that backs
 * exactly what the panel renders.
 *
 * Returns null if the session has no snapshot ≤ `ts` (i.e., the
 * scrubber landed before the first tick). The panel renders the
 * "no data yet" affordance in that case.
 *
 * Indexed by `ix_state_snapshots_session_id_ts_desc` for O(log n)
 * lookup — even a 60-min session is one B-tree descent.
 */
export async function findSnapshotAtOrBefore(
  sessionId: string,
  ts: Date,
): Promise<StateSnapshotRow | null> {
  const row = await db.stateSnapshot.findFirst({
    where: { sessionId, ts: { lte: ts } },
    orderBy: [{ ts: 'desc' }, { id: 'desc' }],
    select: {
      id: true,
      sessionId: true,
      tickId: true,
      ts: true,
      state: true,
    },
  });
  if (row === null) return null;
  return {
    id: row.id,
    sessionId: row.sessionId,
    tickId: row.tickId,
    ts: row.ts,
    state: row.state,
  };
}

export interface SessionFlagRow {
  id: string;
  sessionId: string;
  ts: Date;
  researcherId: string | null;
  note: string | null;
  autoGenerated: boolean;
}

/**
 * Range-fetch session flags by `ts ∈ [fromTs, toTs]`.
 *
 * Flags are sparse (a researcher might drop a dozen bookmarks across
 * an hour; the engine drops auto-flags for major events). Default
 * `limit` is 200 — more than enough for a normal session — with a
 * 500-row hard cap so a misbehaving client can't sweep the table.
 *
 * The replay timeline renders one marker per flag; the click handler
 * jumps the scrubber to `ts` and pops the note in the state panel.
 */
export async function listSessionFlagsByRange(
  sessionId: string,
  options: RangeQueryOptions = {},
): Promise<SessionFlagRow[]> {
  const limit = clampLimit(options.limit, 200, FLAG_RANGE_MAX);
  const ts = tsFilter(options.fromTs, options.toTs);
  const rows = await db.sessionFlag.findMany({
    where: {
      sessionId,
      ...(ts.gte !== undefined || ts.lte !== undefined ? { ts } : {}),
    },
    orderBy: [{ ts: 'asc' }, { id: 'asc' }],
    take: limit,
    select: {
      id: true,
      sessionId: true,
      ts: true,
      researcherId: true,
      note: true,
      autoGenerated: true,
    },
  });
  return rows.map((row) => ({
    id: row.id,
    sessionId: row.sessionId,
    ts: row.ts,
    researcherId: row.researcherId,
    note: row.note,
    autoGenerated: row.autoGenerated,
  }));
}
