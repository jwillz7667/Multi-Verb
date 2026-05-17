/**
 * Curated voice library — TypeScript mirror of the engine's
 * `VOICE_LIBRARY` (services/engine/verbio_engine/voices/library.py).
 *
 * Voices are static curation data, not derived from runtime state, so
 * the canonical list lives in code on both sides. The engine consumes
 * it via the picker + fallback-cache modules; the web consumes it to
 * populate the persona form's voice dropdown.
 *
 * If this list drifts from `library.py` the engine will raise
 * `UnknownVoiceError` at runtime when a persona references a voice the
 * library no longer carries. The `voices.test.ts` cardinality check
 * here is intentionally tight — anyone editing one side without the
 * other should break a test before it ships.
 */
import type { VoiceProvider } from '@/features/studies/persona-types';

export type VoiceFormality = 'casual' | 'neutral' | 'formal';
export type VoiceWarmth = 'warm' | 'neutral' | 'cool';
export type VoicePace = 'brisk' | 'steady' | 'measured';

export interface CuratedVoice {
  voiceId: string;
  provider: VoiceProvider;
  displayName: string;
  formality: VoiceFormality;
  warmth: VoiceWarmth;
  pace: VoicePace;
  description: string;
}

const CARTESIA_VOICES: readonly CuratedVoice[] = [
  {
    voiceId: '79a125e8-cd45-4c13-8a67-188112f4dd22',
    provider: 'cartesia',
    displayName: 'Bridget',
    formality: 'formal',
    warmth: 'neutral',
    pace: 'measured',
    description: 'Crisp British delivery; reads steady and authoritative.',
  },
  {
    voiceId: '156fb8d2-335b-4950-9cb3-a2d33befec77',
    provider: 'cartesia',
    displayName: 'Margot',
    formality: 'neutral',
    warmth: 'warm',
    pace: 'steady',
    description: 'Helpful, approachable; default fit for most studies.',
  },
  {
    voiceId: '2deb3edf-b9d8-4d06-8db9-5742fb8a3cb2',
    provider: 'cartesia',
    displayName: 'Hazel',
    formality: 'casual',
    warmth: 'warm',
    pace: 'steady',
    description: 'Soft and conversational; lowers tension in tense groups.',
  },
  {
    voiceId: 'd46abd1d-2d02-43e8-819f-51fb652c1c61',
    provider: 'cartesia',
    displayName: 'Daniel',
    formality: 'formal',
    warmth: 'cool',
    pace: 'measured',
    description: 'News-reader cadence; reads as neutral and impartial.',
  },
  {
    voiceId: '694f9389-aac1-45b6-b726-9d9369183238',
    provider: 'cartesia',
    displayName: 'Sarah',
    formality: 'neutral',
    warmth: 'warm',
    pace: 'brisk',
    description: 'Quick, engaged delivery; good for short interventions.',
  },
  {
    voiceId: 'c8605446-247c-4d39-acd4-8f4c28aa363c',
    provider: 'cartesia',
    displayName: 'Eleanor',
    formality: 'formal',
    warmth: 'warm',
    pace: 'measured',
    description: 'Considered and unhurried; suits sensitive subject matter.',
  },
  {
    voiceId: '69267136-1bdc-412f-ad78-0caad210fb40',
    provider: 'cartesia',
    displayName: 'Owen',
    formality: 'casual',
    warmth: 'neutral',
    pace: 'steady',
    description: 'Easygoing male voice; for sessions that need to feel informal.',
  },
] as const;

const ELEVENLABS_VOICES: readonly CuratedVoice[] = [
  {
    voiceId: '21m00Tcm4TlvDq8ikWAM',
    provider: 'elevenlabs',
    displayName: 'Rachel',
    formality: 'neutral',
    warmth: 'warm',
    pace: 'steady',
    description: 'Calm female voice; widely-tested default for general use.',
  },
  {
    voiceId: 'EXAVITQu4vr4xnSDxMAC',
    provider: 'elevenlabs',
    displayName: 'Sarah',
    formality: 'neutral',
    warmth: 'warm',
    pace: 'brisk',
    description: 'Soft, friendly delivery; good for engaging quiet participants.',
  },
  {
    voiceId: 'pNInz6obpgDQGcFmaJgB',
    provider: 'elevenlabs',
    displayName: 'Adam',
    formality: 'formal',
    warmth: 'cool',
    pace: 'measured',
    description: 'Deep, professional male voice; reads as impartial.',
  },
  {
    voiceId: 'AZnzlk1XvdvUeBnXmlld',
    provider: 'elevenlabs',
    displayName: 'Domi',
    formality: 'neutral',
    warmth: 'neutral',
    pace: 'brisk',
    description: 'Confident female voice; suits redirect/pressure interventions.',
  },
  {
    voiceId: 'TxGEqnHWrfWFTfGW9XjX',
    provider: 'elevenlabs',
    displayName: 'Josh',
    formality: 'casual',
    warmth: 'warm',
    pace: 'steady',
    description: 'Warm conversational male voice; lowers formality in groups.',
  },
  {
    voiceId: 'MF3mGyEYCl7XYWbV9V6O',
    provider: 'elevenlabs',
    displayName: 'Elli',
    formality: 'casual',
    warmth: 'warm',
    pace: 'brisk',
    description: 'Younger female voice; suits casual or peer-style studies.',
  },
  {
    voiceId: 'VR6AewLTigWG4xSOukaG',
    provider: 'elevenlabs',
    displayName: 'Arnold',
    formality: 'formal',
    warmth: 'cool',
    pace: 'measured',
    description: 'Crisp male voice; reads as composed and unhurried.',
  },
] as const;

export const VOICE_LIBRARY: readonly CuratedVoice[] = [...CARTESIA_VOICES, ...ELEVENLABS_VOICES];

export function listVoicesForProvider(provider: VoiceProvider): readonly CuratedVoice[] {
  return VOICE_LIBRARY.filter((v) => v.provider === provider);
}

export function findVoice(voiceId: string, provider: VoiceProvider): CuratedVoice | undefined {
  return VOICE_LIBRARY.find((v) => v.provider === provider && v.voiceId === voiceId);
}
