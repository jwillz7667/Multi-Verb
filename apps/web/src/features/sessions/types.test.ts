import { describe, expect, it } from 'vitest';

import { CreateSessionInputSchema, MintTokenInputSchema } from './types';

describe('CreateSessionInputSchema', () => {
  it('accepts an empty body and coerces scheduledStart to null', () => {
    const parsed = CreateSessionInputSchema.parse({});
    expect(parsed.scheduledStart).toBeNull();
  });

  it('parses an ISO string into a Date', () => {
    const iso = '2026-06-01T12:00:00.000Z';
    const parsed = CreateSessionInputSchema.parse({ scheduledStart: iso });
    expect(parsed.scheduledStart).toBeInstanceOf(Date);
    expect(parsed.scheduledStart?.toISOString()).toBe(iso);
  });

  it('rejects a non-ISO scheduledStart', () => {
    const result = CreateSessionInputSchema.safeParse({ scheduledStart: 'tomorrow' });
    expect(result.success).toBe(false);
  });

  it('treats empty string as null (form submit edge case)', () => {
    const parsed = CreateSessionInputSchema.parse({ scheduledStart: '' });
    expect(parsed.scheduledStart).toBeNull();
  });
});

describe('MintTokenInputSchema', () => {
  it('requires a UUID sessionId', () => {
    const result = MintTokenInputSchema.safeParse({
      sessionId: 'not-a-uuid',
      displayName: 'Maya',
      role: 'participant',
    });
    expect(result.success).toBe(false);
  });

  it('rejects unknown roles to prevent privilege escalation', () => {
    const result = MintTokenInputSchema.safeParse({
      sessionId: '00000000-0000-0000-0000-000000000000',
      displayName: 'Maya',
      role: 'admin',
    });
    expect(result.success).toBe(false);
  });

  it('rejects empty display names', () => {
    const result = MintTokenInputSchema.safeParse({
      sessionId: '00000000-0000-0000-0000-000000000000',
      displayName: '   ',
      role: 'participant',
    });
    expect(result.success).toBe(false);
  });

  it('accepts a fully-formed input', () => {
    const parsed = MintTokenInputSchema.parse({
      sessionId: '00000000-0000-0000-0000-000000000000',
      displayName: '  Maya  ',
      role: 'researcher',
    });
    // Display name is trimmed by the schema.
    expect(parsed.displayName).toBe('Maya');
    expect(parsed.role).toBe('researcher');
  });
});
