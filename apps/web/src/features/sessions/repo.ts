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
