/**
 * Study feature — typed inputs, outputs, and form schemas.
 *
 * Zod is the boundary validator (brief §10.3 — validate every external
 * input). The engine reads `moderator_persona` as its Pydantic
 * `ModeratorPersona` (extra="forbid"), so the form must produce a
 * compatible shape — that's enforced here by mapping every form field
 * one-to-one onto a persona field with matching constraints.
 *
 * `rulesVersion` is fixed to `V1_RULES_VERSION` for now. When the
 * engine ships a v2 ruleset the form will expose a version picker; for
 * the moment the default is the only valid value and the engine's
 * registry rejects anything else.
 */

import { z } from 'zod';

import {
  PERSONA_FORMALITIES,
  PERSONA_TONES,
  VOICE_PROVIDERS,
  type PersonaFormality,
  type PersonaTone,
  type VoiceProvider,
} from './persona-types';

/** Canonical rules version shipped today. Mirrors `V1_RULES_VERSION` in
 * services/engine/verbio_engine/rules/v1.py. */
export const DEFAULT_RULES_VERSION = 'v1.0';

/** Persona shape stored in `studies.moderator_persona` JSONB.
 *
 * Snake_case fields because the engine consumes them as Pydantic
 * (extra="forbid"); any drift in field names blows up at session start. */
export const ModeratorPersonaSchema = z.object({
  style_prompt: z
    .string()
    .trim()
    .min(1, 'style_prompt is required')
    .max(500, 'style_prompt must be 500 characters or fewer'),
  tone: z.enum(PERSONA_TONES).default('warm'),
  formality: z.enum(PERSONA_FORMALITIES).default('neutral'),
  voice_provider: z.enum(VOICE_PROVIDERS).default('cartesia'),
  voice_id: z.string().trim().min(1, 'voice_id is required'),
});
export type ModeratorPersonaInput = z.infer<typeof ModeratorPersonaSchema>;

export const CreateStudyInputSchema = z.object({
  name: z.string().trim().min(1, 'name is required').max(200),
  prompt: z.string().trim().min(1, 'prompt is required').max(4000),
  moderatorPersona: ModeratorPersonaSchema,
});
export type CreateStudyInput = z.infer<typeof CreateStudyInputSchema>;

/** Update form — every field optional so a partial PATCH only touches
 * what the researcher edited. Persona updates replace the JSONB blob
 * wholesale (no merge) because the engine validates the full shape. */
export const UpdateStudyInputSchema = z.object({
  name: z.string().trim().min(1).max(200).optional(),
  prompt: z.string().trim().min(1).max(4000).optional(),
  moderatorPersona: ModeratorPersonaSchema.optional(),
});
export type UpdateStudyInput = z.infer<typeof UpdateStudyInputSchema>;

export interface StudyRow {
  id: string;
  orgId: string;
  name: string;
  prompt: string;
  rulesVersion: string;
  moderatorPersona: ModeratorPersonaInput;
  createdAt: Date;
  createdBy: string;
}

export type { PersonaFormality, PersonaTone, VoiceProvider };
