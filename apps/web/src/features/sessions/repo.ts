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
