/**
 * Studies feature — client-safe public surface.
 *
 * Client components (`'use client'`) cannot pull the main `index.ts`
 * barrel because it re-exports `service.ts`/`repo.ts`, which carry
 * `import 'server-only'` and explode at build time when bundled into
 * the browser chunk.
 *
 * This barrel re-exports only the pure types, constants, and lookup
 * helpers that have no server-side dependencies. Anything Prisma-
 * backed lives in the server barrel.
 */

export {
  CreateStudyInputSchema,
  DEFAULT_RULES_VERSION,
  ModeratorPersonaSchema,
  UpdateStudyInputSchema,
} from './types';
export type { CreateStudyInput, ModeratorPersonaInput, StudyRow, UpdateStudyInput } from './types';
export { PERSONA_FORMALITIES, PERSONA_TONES, VOICE_PROVIDERS } from './persona-types';
export type { PersonaFormality, PersonaTone, VoiceProvider } from './persona-types';
export { findVoice, listVoicesForProvider, VOICE_LIBRARY } from './voices';
export type { CuratedVoice, VoiceFormality, VoicePace, VoiceWarmth } from './voices';
