/**
 * Studies feature — public surface.
 *
 * Consumers (route handlers, server actions, UI server components)
 * import from this barrel. Deep imports across feature boundaries
 * are forbidden per the project's architecture rules.
 */

export { createNewStudy, getStudy, listStudies, updateExistingStudy } from './service';
export { StudyNotFoundError } from './repo';
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
