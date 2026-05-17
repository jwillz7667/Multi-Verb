/**
 * Study repository — Prisma-backed reads + writes scoped to one org.
 *
 * Tenancy: studies carry `orgId`; every read filters on it explicitly.
 * Until the org concept lands (a later phase), the Auth.js user id
 * doubles as the org id — single-user MVP is a degenerate case of the
 * multi-tenant model and the API doesn't change when an `orgId` field
 * appears on `User`.
 *
 * The repo speaks the typed `StudyRow` DTO. `moderatorPersona` is
 * JSONB on the wire; we Zod-parse it on read so a corrupted row
 * (manual SQL edit, schema drift) fails loudly here instead of
 * propagating untyped data into the form layer.
 */

import 'server-only';

import { randomUUID } from 'node:crypto';

import type { Prisma } from '@/generated/prisma';
import { db } from '@/lib/db';

import {
  DEFAULT_RULES_VERSION,
  ModeratorPersonaSchema,
  type ModeratorPersonaInput,
  type StudyRow,
} from './types';

export class StudyNotFoundError extends Error {
  readonly code = 'study_not_found';
  constructor(public readonly studyId: string) {
    super(`Study ${studyId} not found`);
    this.name = 'StudyNotFoundError';
  }
}

interface CreateStudyDbInput {
  orgId: string;
  name: string;
  prompt: string;
  moderatorPersona: ModeratorPersonaInput;
  createdBy: string;
}

/** Cast a JSONB column to a record without `as any`. Prisma types JSON
 * as `JsonValue`, which is broader than what we ever write — narrow at
 * the boundary, then Zod-parse to the canonical shape. */
function asRecord(value: unknown): Record<string, unknown> {
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function toStudyRow(row: {
  id: string;
  orgId: string;
  name: string;
  prompt: string;
  rulesVersion: string;
  moderatorPersona: unknown;
  createdAt: Date;
  createdBy: string;
}): StudyRow {
  return {
    id: row.id,
    orgId: row.orgId,
    name: row.name,
    prompt: row.prompt,
    rulesVersion: row.rulesVersion,
    moderatorPersona: ModeratorPersonaSchema.parse(asRecord(row.moderatorPersona)),
    createdAt: row.createdAt,
    createdBy: row.createdBy,
  };
}

export async function createStudy(input: CreateStudyDbInput): Promise<StudyRow> {
  const id = randomUUID();
  const row = await db.study.create({
    data: {
      id,
      orgId: input.orgId,
      name: input.name,
      prompt: input.prompt,
      rulesVersion: DEFAULT_RULES_VERSION,
      moderatorPersona: input.moderatorPersona,
      createdBy: input.createdBy,
    },
    select: {
      id: true,
      orgId: true,
      name: true,
      prompt: true,
      rulesVersion: true,
      moderatorPersona: true,
      createdAt: true,
      createdBy: true,
    },
  });
  return toStudyRow(row);
}

export async function findStudyById(studyId: string, orgId: string): Promise<StudyRow | null> {
  const row = await db.study.findFirst({
    where: { id: studyId, orgId },
    select: {
      id: true,
      orgId: true,
      name: true,
      prompt: true,
      rulesVersion: true,
      moderatorPersona: true,
      createdAt: true,
      createdBy: true,
    },
  });
  if (row === null) return null;
  return toStudyRow(row);
}

export async function listStudiesForOrg(orgId: string, limit = 50): Promise<StudyRow[]> {
  const rows = await db.study.findMany({
    where: { orgId },
    orderBy: { createdAt: 'desc' },
    take: limit,
    select: {
      id: true,
      orgId: true,
      name: true,
      prompt: true,
      rulesVersion: true,
      moderatorPersona: true,
      createdAt: true,
      createdBy: true,
    },
  });
  return rows.map(toStudyRow);
}

// `exactOptionalPropertyTypes` is on: optional fields explicitly include
// `| undefined` to match Zod's `.optional()` inferred shape. Callers
// (service.ts) pass the Zod-parsed patch directly without filtering.
interface UpdateStudyPatch {
  name?: string | undefined;
  prompt?: string | undefined;
  moderatorPersona?: ModeratorPersonaInput | undefined;
}

export async function updateStudy(
  studyId: string,
  orgId: string,
  patch: UpdateStudyPatch,
): Promise<StudyRow> {
  // Scope check first — `updateMany` returns a count, not the row, so
  // an existence + ownership check up front lets us throw the precise
  // not-found error and keep the update narrow.
  const existing = await findStudyById(studyId, orgId);
  if (existing === null) throw new StudyNotFoundError(studyId);

  const data: Prisma.StudyUpdateInput = {};
  if (patch.name !== undefined) data.name = patch.name;
  if (patch.prompt !== undefined) data.prompt = patch.prompt;
  if (patch.moderatorPersona !== undefined) {
    data.moderatorPersona = patch.moderatorPersona;
  }

  const row = await db.study.update({
    where: { id: studyId },
    data,
    select: {
      id: true,
      orgId: true,
      name: true,
      prompt: true,
      rulesVersion: true,
      moderatorPersona: true,
      createdAt: true,
      createdBy: true,
    },
  });
  return toStudyRow(row);
}
