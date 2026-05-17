import { describe, expect, it } from 'vitest';

import { findVoice, listVoicesForProvider, VOICE_LIBRARY } from './voices';

describe('VOICE_LIBRARY', () => {
  // These cardinality checks catch a single-side edit between this
  // file and services/engine/verbio_engine/voices/library.py. If you
  // add or remove a voice on one side, bump the expectation here.
  it('contains exactly 14 voices across both providers', () => {
    expect(VOICE_LIBRARY).toHaveLength(14);
  });

  it('contains 7 cartesia voices', () => {
    expect(listVoicesForProvider('cartesia')).toHaveLength(7);
  });

  it('contains 7 elevenlabs voices', () => {
    expect(listVoicesForProvider('elevenlabs')).toHaveLength(7);
  });

  it('keeps every (provider, voiceId) pair unique', () => {
    const keys = VOICE_LIBRARY.map((v) => `${v.provider}:${v.voiceId}`);
    expect(new Set(keys).size).toBe(VOICE_LIBRARY.length);
  });

  it('uses only formality, warmth and pace values the engine knows', () => {
    const formalities = new Set(['casual', 'neutral', 'formal']);
    const warmths = new Set(['warm', 'neutral', 'cool']);
    const paces = new Set(['brisk', 'steady', 'measured']);
    for (const voice of VOICE_LIBRARY) {
      expect(formalities.has(voice.formality)).toBe(true);
      expect(warmths.has(voice.warmth)).toBe(true);
      expect(paces.has(voice.pace)).toBe(true);
    }
  });
});

describe('findVoice', () => {
  it('returns the voice when (id, provider) is in the library', () => {
    const voice = findVoice('156fb8d2-335b-4950-9cb3-a2d33befec77', 'cartesia');
    expect(voice?.displayName).toBe('Margot');
  });

  it('returns undefined when provider mismatches the voice id', () => {
    // Cartesia id queried against elevenlabs must not match.
    expect(findVoice('156fb8d2-335b-4950-9cb3-a2d33befec77', 'elevenlabs')).toBeUndefined();
  });

  it('returns undefined for an unknown id', () => {
    expect(findVoice('not-a-real-id', 'cartesia')).toBeUndefined();
  });
});
