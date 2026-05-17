import { describe, expect, it } from 'vitest';

import {
  CreateStudyInputSchema,
  DEFAULT_RULES_VERSION,
  ModeratorPersonaSchema,
  UpdateStudyInputSchema,
} from './types';

describe('ModeratorPersonaSchema', () => {
  const minimal = {
    style_prompt: 'You are a warm, neutral moderator.',
    voice_id: '156fb8d2-335b-4950-9cb3-a2d33befec77',
  };

  it('fills tone, formality and provider with brief-aligned defaults', () => {
    const parsed = ModeratorPersonaSchema.parse(minimal);
    expect(parsed.tone).toBe('warm');
    expect(parsed.formality).toBe('neutral');
    expect(parsed.voice_provider).toBe('cartesia');
  });

  it('rejects an empty style_prompt', () => {
    const result = ModeratorPersonaSchema.safeParse({ ...minimal, style_prompt: '   ' });
    expect(result.success).toBe(false);
  });

  it('rejects style_prompt over 500 characters — matches engine Field(max_length=500)', () => {
    const result = ModeratorPersonaSchema.safeParse({
      ...minimal,
      style_prompt: 'x'.repeat(501),
    });
    expect(result.success).toBe(false);
  });

  it('rejects an unknown tone', () => {
    const result = ModeratorPersonaSchema.safeParse({ ...minimal, tone: 'fiery' });
    expect(result.success).toBe(false);
  });

  it('rejects an unknown voice provider', () => {
    const result = ModeratorPersonaSchema.safeParse({ ...minimal, voice_provider: 'azure' });
    expect(result.success).toBe(false);
  });

  it('rejects an empty voice_id', () => {
    const result = ModeratorPersonaSchema.safeParse({ ...minimal, voice_id: '' });
    expect(result.success).toBe(false);
  });
});

describe('CreateStudyInputSchema', () => {
  const valid = {
    name: 'Q2 banking pilot',
    prompt: 'How do you currently move money between accounts?',
    moderatorPersona: {
      style_prompt: 'You are a careful, neutral moderator.',
      voice_id: '156fb8d2-335b-4950-9cb3-a2d33befec77',
    },
  };

  it('accepts a fully-formed input and applies persona defaults', () => {
    const parsed = CreateStudyInputSchema.parse(valid);
    expect(parsed.name).toBe('Q2 banking pilot');
    expect(parsed.moderatorPersona.formality).toBe('neutral');
  });

  it('trims whitespace from name and prompt', () => {
    const parsed = CreateStudyInputSchema.parse({
      ...valid,
      name: '  Pilot  ',
      prompt: '  prompt body  ',
    });
    expect(parsed.name).toBe('Pilot');
    expect(parsed.prompt).toBe('prompt body');
  });

  it('rejects an empty name', () => {
    const result = CreateStudyInputSchema.safeParse({ ...valid, name: '   ' });
    expect(result.success).toBe(false);
  });

  it('rejects a prompt over 4000 characters', () => {
    const result = CreateStudyInputSchema.safeParse({ ...valid, prompt: 'x'.repeat(4001) });
    expect(result.success).toBe(false);
  });

  it('requires a moderatorPersona — engine cannot speak without one', () => {
    const result = CreateStudyInputSchema.safeParse({
      name: valid.name,
      prompt: valid.prompt,
    });
    expect(result.success).toBe(false);
  });
});

describe('UpdateStudyInputSchema', () => {
  it('accepts an empty patch (no-op update)', () => {
    const parsed = UpdateStudyInputSchema.parse({});
    expect(parsed).toEqual({});
  });

  it('accepts a partial patch with only name', () => {
    const parsed = UpdateStudyInputSchema.parse({ name: 'Renamed' });
    expect(parsed.name).toBe('Renamed');
    expect(parsed.prompt).toBeUndefined();
    expect(parsed.moderatorPersona).toBeUndefined();
  });

  it('still validates the persona when present', () => {
    const result = UpdateStudyInputSchema.safeParse({
      moderatorPersona: { style_prompt: '', voice_id: 'v-1' },
    });
    expect(result.success).toBe(false);
  });
});

describe('DEFAULT_RULES_VERSION', () => {
  it('pins to v1.0 — the engine rejects anything else today', () => {
    expect(DEFAULT_RULES_VERSION).toBe('v1.0');
  });
});
