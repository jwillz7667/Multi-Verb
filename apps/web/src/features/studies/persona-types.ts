/**
 * Persona enum constants — single source for runtime + form validation.
 *
 * The engine ships these as Pydantic Literals (see
 * `services/engine/verbio_engine/mouth/persona.py`); the generated
 * `ModeratorPersona` type in `@verbio/shared-types` carries the same
 * unions, but only as type-level constraints. We need them as runtime
 * arrays here for Zod enums and `<select>` option rendering, so they
 * are mirrored explicitly. If the engine adds a new tone or provider,
 * the corresponding `as const` tuple below must update too — the
 * `persona-types.test.ts` cardinality check catches single-side edits.
 */

export const PERSONA_TONES = ['warm', 'neutral', 'professional'] as const;
export type PersonaTone = (typeof PERSONA_TONES)[number];

export const PERSONA_FORMALITIES = ['casual', 'neutral', 'formal'] as const;
export type PersonaFormality = (typeof PERSONA_FORMALITIES)[number];

export const VOICE_PROVIDERS = ['cartesia', 'elevenlabs'] as const;
export type VoiceProvider = (typeof VOICE_PROVIDERS)[number];
