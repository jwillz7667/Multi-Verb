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
    if (parsed.type !== 'utterance') throw new Error('expected utterance variant');
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

  it('rejects an unknown type literal (discriminated union)', () => {
    // `decision` is a valid variant since Phase 3 L12; use a sentinel
    // that no envelope class would ever claim.
    const result = transcriptEventSchema.safeParse({ ...VALID_EVENT, type: 'unknown_variant' });
    expect(result.success).toBe(false);
  });

  it('rejects an utterance payload smuggled under the state_snapshot type', () => {
    // The discriminated union picks the state_snapshot variant on
    // `type`; the utterance payload then fails the snapshot payload
    // shape check, which is exactly what we want — variants can't bleed.
    const result = transcriptEventSchema.safeParse({ ...VALID_EVENT, type: 'state_snapshot' });
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

// Mirrors what the engine actually emits via Pydantic's
// `model_dump(mode="json")` — every defaulted field present, nulls
// where the value is genuinely absent (last_spoke_at on a newcomer).
function makeParticipant(
  overrides: Partial<{
    participant_id: string;
    display_name: string;
    joined_at: string;
    is_currently_speaking: boolean;
    last_spoke_at: string | null;
    last_spoke_duration_sec: number | null;
    speaking_time_total_sec: number;
    turn_count: number;
  }> = {},
) {
  return {
    participant_id: '22222222-2222-4222-8222-222222222222',
    display_name: 'Maya',
    joined_at: '2026-05-16T12:30:30.000Z',
    speaking_time_total_sec: 12.5,
    speaking_time_last_5min_sec: 5.0,
    speaking_time_last_60sec: 1.0,
    turn_count: 4,
    last_spoke_at: '2026-05-16T12:34:50.000Z',
    last_spoke_duration_sec: 3.2,
    is_currently_speaking: true,
    vad_active: true,
    backchannel_count_last_2min: 1,
    interruption_count: 0,
    was_interrupted_count: 0,
    recent_utterances: [],
    rolling_transcript_2min: '',
    flags: {
      dominating: false,
      silent_too_long: false,
      frequently_interrupted: false,
      disengaged: false,
    },
    fair_share_pct: 25,
    actual_share_last_5min_pct: 30,
    ...overrides,
  };
}

const VALID_SNAPSHOT_EVENT = {
  type: 'state_snapshot' as const,
  id: '33333333-3333-4333-8333-333333333333',
  session_id: '11111111-1111-4111-8111-111111111111',
  ts: '2026-05-16T12:34:56.789Z',
  payload: {
    snapshot_id: '33333333-3333-4333-8333-333333333333',
    state: {
      session_id: '11111111-1111-4111-8111-111111111111',
      tick_id: 42,
      t: '2026-05-16T12:34:56.500Z',
      started_at: '2026-05-16T12:30:00.000Z',
      scheduled_end_at: null,
      elapsed_sec: 296.5,
      participants: {
        '22222222-2222-4222-8222-222222222222': makeParticipant(),
      },
      currently_speaking_count: 1,
      silence_run_sec: 0,
      rolling_global_transcript_2min: 'so what i think is...',
      is_paused: false,
      moderator_muted: false,
      quietness_budget: {
        current_window_count: 0,
        last_utterance_at: null,
        max_utterances_per_10min: 8,
        min_seconds_between_utterances: 15,
      },
    },
  },
};

describe('transcriptEventSchema — state_snapshot variant', () => {
  it('accepts a well-formed state_snapshot envelope', () => {
    const parsed = transcriptEventSchema.parse(VALID_SNAPSHOT_EVENT);
    if (parsed.type !== 'state_snapshot') throw new Error('expected snapshot variant');
    expect(parsed.payload.snapshot_id).toBe(VALID_SNAPSHOT_EVENT.payload.snapshot_id);
    expect(parsed.payload.state.tick_id).toBe(42);
    expect(
      parsed.payload.state.participants['22222222-2222-4222-8222-222222222222']?.display_name,
    ).toBe('Maya');
  });

  it('accepts a snapshot with an empty participants map', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_SNAPSHOT_EVENT,
      payload: {
        ...VALID_SNAPSHOT_EVENT.payload,
        state: { ...VALID_SNAPSHOT_EVENT.payload.state, participants: {} },
      },
    });
    expect(result.success).toBe(true);
  });

  it('tolerates unknown nested SessionState keys (forward-compat)', () => {
    // Default Zod "strip" mode silently drops unknown fields on the
    // nested state projection; envelope-level keys remain strict. This
    // lets the engine roll out a new derived field without coordinating
    // a web release.
    const result = transcriptEventSchema.safeParse({
      ...VALID_SNAPSHOT_EVENT,
      payload: {
        ...VALID_SNAPSHOT_EVENT.payload,
        state: { ...VALID_SNAPSHOT_EVENT.payload.state, future_field: 'ok' },
      },
    });
    expect(result.success).toBe(true);
  });

  it('rejects unknown envelope keys on the snapshot variant', () => {
    const result = transcriptEventSchema.safeParse({ ...VALID_SNAPSHOT_EVENT, extra: 'nope' });
    expect(result.success).toBe(false);
  });

  it('rejects unknown payload keys on the snapshot variant', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_SNAPSHOT_EVENT,
      payload: { ...VALID_SNAPSHOT_EVENT.payload, sneaky: 'field' },
    });
    expect(result.success).toBe(false);
  });

  it('rejects a malformed snapshot_id', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_SNAPSHOT_EVENT,
      payload: { ...VALID_SNAPSHOT_EVENT.payload, snapshot_id: 'not-a-uuid' },
    });
    expect(result.success).toBe(false);
  });

  it('rejects a negative tick_id', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_SNAPSHOT_EVENT,
      payload: {
        ...VALID_SNAPSHOT_EVENT.payload,
        state: { ...VALID_SNAPSHOT_EVENT.payload.state, tick_id: -1 },
      },
    });
    expect(result.success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Decision variant (Phase 3 L12) — shadow-mode wire shape.
// ---------------------------------------------------------------------------

const VALID_DECISION_EVENT = {
  type: 'decision' as const,
  id: '44444444-4444-4444-8444-444444444444',
  session_id: '11111111-1111-4111-8111-111111111111',
  ts: '2026-05-16T12:34:56.500Z',
  payload: {
    decision: {
      decision_id: '44444444-4444-4444-8444-444444444444',
      session_id: '11111111-1111-4111-8111-111111111111',
      tick_id: 17,
      timestamp: '2026-05-16T12:34:56.500Z',
      action: 'stay_silent' as const,
      target_participant_id: null,
      source: 'auto' as const,
      triggering_rule: null,
      researcher_id: null,
      researcher_hint: null,
      reason_codes: [],
      reason_human: '',
      confidence: 0.0,
      suppressed_by: ['quietness_budget'],
      was_executed: false,
      llm_prompt: null,
      llm_output: null,
      tts_audio_url: null,
      spoken_at: null,
      cooldown_until: '2026-05-16T12:34:56.500Z',
    },
  },
};

describe('transcriptEventSchema — decision variant', () => {
  it('accepts a well-formed shadow-mode stay_silent decision', () => {
    const parsed = transcriptEventSchema.parse(VALID_DECISION_EVENT);
    if (parsed.type !== 'decision') throw new Error('expected decision variant');
    expect(parsed.payload.decision.action).toBe('stay_silent');
    expect(parsed.payload.decision.was_executed).toBe(false);
    expect(parsed.payload.decision.suppressed_by).toEqual(['quietness_budget']);
  });

  it('accepts an auto-source decision with a triggering rule + target', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_DECISION_EVENT,
      payload: {
        decision: {
          ...VALID_DECISION_EVENT.payload.decision,
          action: 'prompt_participant',
          triggering_rule: 'unheard_participant',
          target_participant_id: '22222222-2222-4222-8222-222222222222',
          confidence: 0.78,
          reason_codes: ['p2_unheard_240s'],
          reason_human: "Maya hasn't spoken in 4 minutes.",
        },
      },
    });
    expect(result.success).toBe(true);
  });

  it('rejects an unknown action enum value', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_DECISION_EVENT,
      payload: {
        decision: { ...VALID_DECISION_EVENT.payload.decision, action: 'pirouette' },
      },
    });
    expect(result.success).toBe(false);
  });

  it('rejects unknown envelope keys on the decision variant', () => {
    const result = transcriptEventSchema.safeParse({ ...VALID_DECISION_EVENT, extra: 'nope' });
    expect(result.success).toBe(false);
  });

  it('rejects unknown payload keys on the decision variant', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_DECISION_EVENT,
      payload: { ...VALID_DECISION_EVENT.payload, sneaky: 'field' },
    });
    expect(result.success).toBe(false);
  });

  it('rejects confidence outside [0, 1]', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_DECISION_EVENT,
      payload: {
        decision: { ...VALID_DECISION_EVENT.payload.decision, confidence: 1.4 },
      },
    });
    expect(result.success).toBe(false);
  });

  it('rejects a negative tick_id on the decision', () => {
    const result = transcriptEventSchema.safeParse({
      ...VALID_DECISION_EVENT,
      payload: {
        decision: { ...VALID_DECISION_EVENT.payload.decision, tick_id: -1 },
      },
    });
    expect(result.success).toBe(false);
  });
});
