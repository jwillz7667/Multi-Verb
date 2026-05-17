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
