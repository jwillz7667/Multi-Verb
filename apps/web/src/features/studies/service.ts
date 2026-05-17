/**
 * Study orchestration — composes the repo behind a typed surface.
 *
 * Route handlers and server actions consume this layer, not the repo
 * directly. Keeps Prisma calls out of the transport layer and lets
 * tests substitute an in-memory double.
 *
 * Today the orchestration is thin (no LiveKit, no Redis publish) but
 * keeping the seam means the persona-cache-warming work in §9 can hook
 * in here later without rewriting call sites.
 */

import 'server-only';

import { createStudy, findStudyById, listStudiesForOrg, updateStudy } from './repo';

import type { CreateStudyInput, StudyRow, UpdateStudyInput } from './types';

interface CreateStudyCommand {
  input: CreateStudyInput;
  orgId: string;
  createdBy: string;
}

export async function createNewStudy(cmd: CreateStudyCommand): Promise<StudyRow> {
  return createStudy({
    orgId: cmd.orgId,
    name: cmd.input.name,
    prompt: cmd.input.prompt,
    moderatorPersona: cmd.input.moderatorPersona,
    createdBy: cmd.createdBy,
  });
}

interface UpdateStudyCommand {
  studyId: string;
  orgId: string;
  patch: UpdateStudyInput;
}

export async function updateExistingStudy(cmd: UpdateStudyCommand): Promise<StudyRow> {
  return updateStudy(cmd.studyId, cmd.orgId, cmd.patch);
}

export async function getStudy(studyId: string, orgId: string): Promise<StudyRow | null> {
  return findStudyById(studyId, orgId);
}

export async function listStudies(orgId: string): Promise<StudyRow[]> {
  return listStudiesForOrg(orgId);
}
