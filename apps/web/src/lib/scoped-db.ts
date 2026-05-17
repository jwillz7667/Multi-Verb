/**
 * Tenant-scoped Prisma facade. Brief §10.3 / ADR-0002.
 *
 * Verbio enforces tenant isolation at the application layer, not via
 * Postgres RLS. Every web read against a tenant-owned model must go
 * through `scopedDb(orgId)` so the `orgId` filter is automatic and
 * impossible to forget at the call site. Direct `db.<tenantModel>`
 * access outside this file is banned by an ESLint rule (see
 * `apps/web/eslint.config.mjs`) — the few legitimate exceptions
 * (admin cron, the LiveKit egress webhook from LiveKit Cloud) are
 * pinned to specific files via the allowlist.
 *
 * The schema's tenancy graph (apps/web/prisma/schema.prisma):
 *   - `Study.orgId`                    — direct
 *   - `ModeratedSession.studyId`       — joined: scope via `study.orgId`
 *   - everything else (participants,
 *     utterances, decisions, snapshots,
 *     flags, researcher_actions)        — joined through session.study
 *
 * Sessions with `studyId === null` exist (Phase 2 created them before
 * the studies table landed). They are intentionally unreachable from
 * any per-org scope and are only swept by the admin/cron retention
 * runner via `unscopedDbForAdminCron`.
 *
 * Scope choices for L1:
 *
 *   - Narrow surface — only the lookups currently used by Phase 6
 *     entry points (studies + sessions). The rest land in P7 L2 as
 *     callers migrate. Building a one-shot wrapper over the entire
 *     Prisma surface now would either be vapor (most methods unused)
 *     or push the PR past the 30-min review budget.
 *
 *   - Return projections, not raw Prisma rows. Tenancy is enforced in
 *     the `where` clause; the return shape is the same row type the
 *     repos already publish, so call sites don't see a Prisma object
 *     leak (the existing `findSessionById` etc. already do this).
 */

import 'server-only';

import type { SessionStatus } from '@/features/sessions/types';
import type { PrismaClient } from '@/generated/prisma';
import { db } from '@/lib/db';

export interface ScopedStudyRow {
  id: string;
  orgId: string;
  name: string;
  createdAt: Date;
}

export interface ScopedSessionRow {
  id: string;
  studyId: string;
  livekitRoomName: string;
  status: SessionStatus;
  scheduledStart: Date | null;
  actualStart: Date | null;
  actualEnd: Date | null;
  createdAt: Date;
}

export interface ScopedDb {
  studies: {
    findById(studyId: string): Promise<ScopedStudyRow | null>;
    list(limit?: number): Promise<ScopedStudyRow[]>;
  };
  sessions: {
    findById(sessionId: string): Promise<ScopedSessionRow | null>;
    findByLivekitRoomName(roomName: string): Promise<ScopedSessionRow | null>;
  };
}

interface ScopedDbOptions {
  /**
   * Override the underlying Prisma client. Tests pass a fake; the
   * production default is the shared `db` singleton.
   */
  prisma?: PrismaClient;
}

/**
 * Build a tenant-scoped facade over Prisma for one org. Every method
 * injects the org filter automatically; cross-tenant rows return
 * `null` (lookups) or are absent (list).
 *
 * `orgId` is the caller's resolved org uuid — production callers go
 * through `orgIdForUser(session.user.id)` (lib/identity.ts).
 */
export function scopedDb(orgId: string, options: ScopedDbOptions = {}): ScopedDb {
  if (typeof orgId !== 'string' || orgId.length === 0) {
    // Fail fast: an empty orgId would broaden every query to "any row"
    // and is never something a caller should pass. We'd rather crash
    // the request than silently leak rows across tenants.
    throw new Error('scopedDb: orgId must be a non-empty string');
  }
  const prisma = options.prisma ?? db;
  return {
    studies: {
      async findById(studyId: string): Promise<ScopedStudyRow | null> {
        const row = await prisma.study.findFirst({
          where: { id: studyId, orgId },
          select: { id: true, orgId: true, name: true, createdAt: true },
        });
        return row;
      },
      async list(limit = 50): Promise<ScopedStudyRow[]> {
        const rows = await prisma.study.findMany({
          where: { orgId },
          orderBy: [{ createdAt: 'desc' }, { id: 'desc' }],
          take: limit,
          select: { id: true, orgId: true, name: true, createdAt: true },
        });
        return rows;
      },
    },
    sessions: {
      async findById(sessionId: string): Promise<ScopedSessionRow | null> {
        // `study: { orgId }` requires a non-null studyId AND a matching
        // org. Sessions without a study (studyId === null) are
        // unreachable from any per-org scope — they only exist for the
        // admin cron sweep.
        const row = await prisma.moderatedSession.findFirst({
          where: { id: sessionId, study: { orgId } },
          select: {
            id: true,
            studyId: true,
            livekitRoomName: true,
            status: true,
            scheduledStart: true,
            actualStart: true,
            actualEnd: true,
            createdAt: true,
          },
        });
        if (row?.studyId == null) return null;
        return {
          id: row.id,
          studyId: row.studyId,
          livekitRoomName: row.livekitRoomName,
          status: row.status as SessionStatus,
          scheduledStart: row.scheduledStart,
          actualStart: row.actualStart,
          actualEnd: row.actualEnd,
          createdAt: row.createdAt,
        };
      },
      async findByLivekitRoomName(roomName: string): Promise<ScopedSessionRow | null> {
        const row = await prisma.moderatedSession.findFirst({
          where: { livekitRoomName: roomName, study: { orgId } },
          select: {
            id: true,
            studyId: true,
            livekitRoomName: true,
            status: true,
            scheduledStart: true,
            actualStart: true,
            actualEnd: true,
            createdAt: true,
          },
        });
        if (row?.studyId == null) return null;
        return {
          id: row.id,
          studyId: row.studyId,
          livekitRoomName: row.livekitRoomName,
          status: row.status as SessionStatus,
          scheduledStart: row.scheduledStart,
          actualStart: row.actualStart,
          actualEnd: row.actualEnd,
          createdAt: row.createdAt,
        };
      },
    },
  };
}

/**
 * Escape hatch — return the raw, unscoped Prisma client.
 *
 * Use only for processes that legitimately need to walk every tenant:
 *
 *   - The daily retention cron (`/api/admin/retention/run`) deletes
 *     across all orgs by design.
 *   - The LiveKit egress webhook (`/api/livekit/webhook`) is hit by
 *     LiveKit Cloud, not a user — it resolves the session by
 *     `livekit_room_name` (globally unique) and only updates that one
 *     row. No tenant context exists at the webhook boundary.
 *
 * The `reason` string is required so the call site reads as a
 * deliberate, reviewable escape — `unscopedDbForAdminCron('daily
 * retention sweep')` jumps out in code review where a bare `db` import
 * would not. P7 L4 will additionally surface it via the structured
 * logger; until then, the call-site literal is the audit trail.
 */
export function unscopedDbForAdminCron(reason: string): PrismaClient {
  if (typeof reason !== 'string' || reason.length === 0) {
    throw new Error('unscopedDbForAdminCron: a justification string is required');
  }
  return db;
}
