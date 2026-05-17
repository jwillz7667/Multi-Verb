/**
 * AUTO-GENERATED — DO NOT EDIT BY HAND.
 *
 * Source: Pydantic models in services/engine/verbio_engine/domain/.
 * Regenerate via: pnpm shared-types:generate
 */

/* eslint-disable */

/**
 * How conversational the phrasing should feel.
 */
export type Formality = 'casual' | 'neutral' | 'formal';
/**
 * Persona-flavored prefix prepended to the §8.2 system message. Keep short (1-2 sentences). Avoid prescribing what the moderator says — that is the engine's job, not the persona.
 */
export type StylePrompt = string;
/**
 * Base affective register; combined with per-action hints.
 */
export type Tone = 'warm' | 'neutral' | 'professional';
/**
 * Provider-specific voice id; picked from the curated library.
 */
export type VoiceId = string;
/**
 * TTS provider whose voice library owns `voice_id`.
 */
export type VoiceProvider = 'cartesia' | 'elevenlabs';

/**
 * Frozen persona configuration for one study.
 */
export interface ModeratorPersona {
  formality?: Formality;
  style_prompt: StylePrompt;
  tone?: Tone;
  voice_id: VoiceId;
  voice_provider?: VoiceProvider;
}
