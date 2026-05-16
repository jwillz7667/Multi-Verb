import { describe, expect, it } from 'vitest';

import { parseTranscriptEvent, transcriptEventSchema } from './events';

const VALID_EVENT = {
  type: 'utterance' as const,
  id: '0d4f3b1e-1234-4cba-9abc-0123456789ab',
  session_id: '11111111-1111-4111-8111-111111111111',
  ts: '2026-05-16T12:34:56.789Z',
  payload: {
    utterance_id: '0d4f3b1e-1234-4cba-9abc-0123456789ab',
    session_id: '11111111-1111-4111-8111-111111111111',
    participant_id: '22222222-2222-4222-8222-222222222222',
    participant_identity: 'p-1',
    participant_display_name: 'Maya',
    text: 'hello world',
    is_final: true,
    confidence: 0.91,
    start_ts: '2026-05-16T12:34:50.000Z',
    end_ts: '2026-05-16T12:34:55.500Z',
  },
};

describe('transcriptEventSchema', () => {
  it('accepts a well-formed utterance envelope', () => {
    const parsed = transcriptEventSchema.parse(VALID_EVENT);
    expect(parsed.id).toBe(VALID_EVENT.id);
    expect(parsed.payload.text).toBe('hello world');
  });

  it('accepts a null confidence', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_EVENT,
      payload: { ...VALID_EVENT.payload, confidence: null },
    });
    expect(result.success).toBe(true);
  });

  it('rejects a missing confidence (engine always emits the field)', () => {
    const { confidence: _confidence, ...rest } = VALID_EVENT.payload;
    void _confidence;
    const result = transcriptEventSchema.safeParse({
      ...VALID_EVENT,
      payload: rest,
    });
    expect(result.success).toBe(false);
  });

  it('rejects unknown top-level keys (strict envelope)', () => {
    const result = transcriptEventSchema.safeParse({ ...VALID_EVENT, extra: 'nope' });
    expect(result.success).toBe(false);
  });

  it('rejects unknown payload keys (strict payload)', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_EVENT,
      payload: { ...VALID_EVENT.payload, sneaky: 'field' },
    });
    expect(result.success).toBe(false);
  });

  it('rejects a non-utterance type literal', () => {
    const result = transcriptEventSchema.safeParse({ ...VALID_EVENT, type: 'decision' });
    expect(result.success).toBe(false);
  });

  it('rejects confidence outside [0, 1]', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_EVENT,
      payload: { ...VALID_EVENT.payload, confidence: 1.5 },
    });
    expect(result.success).toBe(false);
  });

  it('rejects empty participant identity', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_EVENT,
      payload: { ...VALID_EVENT.payload, participant_identity: '' },
    });
    expect(result.success).toBe(false);
  });

  it('rejects malformed ISO timestamps', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_EVENT,
      ts: 'yesterday',
    });
    expect(result.success).toBe(false);
  });
});

describe('parseTranscriptEvent', () => {
  it('returns the parsed event when valid', () => {
    const parsed = parseTranscriptEvent(VALID_EVENT);
    expect(parsed).not.toBeNull();
    expect(parsed?.id).toBe(VALID_EVENT.id);
  });

  it('returns null on malformed input rather than throwing', () => {
    expect(parseTranscriptEvent({ broken: true })).toBeNull();
    expect(parseTranscriptEvent(null)).toBeNull();
    expect(parseTranscriptEvent('not json')).toBeNull();
  });
});
