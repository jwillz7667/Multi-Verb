/**
 * Tests for the transcript formatters (.txt + .vtt).
 *
 * The formatters are pure → cheap to pin down with snapshots and
 * unit-level edge cases. Coverage:
 *
 *   - merged ordering: participant + moderator lines interleave by
 *     `(startMs, speaker, text)`,
 *   - empty / non-final utterances are skipped,
 *   - non-executed decisions and decisions without `llm_output` are
 *     skipped (no fabricated moderator turns),
 *   - lines before the anchor clamp to 0,
 *   - WebVTT escapes `&` / `<` in cue body and strips `<>` in the
 *     voice tag,
 *   - moderator duration scales with word count between min and max
 *     bounds.
 */
import { describe, expect, it } from 'vitest';

import type { DecisionRow, UtteranceWithSpeakerRow } from '@/features/sessions';

import {
  buildTranscriptLines,
  formatTranscriptTxt,
  formatTranscriptVtt,
  type TranscriptHeader,
  type TranscriptLine,
} from './transcript';

const ANCHOR = new Date('2026-05-01T10:00:00.000Z');

function makeUtterance(overrides: Partial<UtteranceWithSpeakerRow>): UtteranceWithSpeakerRow {
  return {
    id: 'u-default',
    sessionId: 'sess-1',
    participantId: 'p-default',
    participantIdentity: 'default-001',
    participantDisplayName: 'Alice',
    text: 'hello world',
    isFinal: true,
    confidence: 0.95,
    startTs: new Date('2026-05-01T10:00:05.000Z'),
    endTs: new Date('2026-05-01T10:00:07.500Z'),
    ...overrides,
  };
}

function makeDecision(overrides: Partial<DecisionRow>): DecisionRow {
  return {
    id: 'd-default',
    sessionId: 'sess-1',
    tickId: 0n,
    ts: new Date('2026-05-01T10:00:10.000Z'),
    action: 'prompt',
    targetParticipantId: null,
    source: 'rules_engine',
    triggeringRule: 'unheard_participant',
    researcherId: null,
    researcherHint: null,
    reasonCodes: [],
    reasonHuman: '',
    confidence: 0.7,
    suppressedBy: [],
    wasExecuted: true,
    llmPrompt: null,
    llmOutput: 'Bob, what is your take on this?',
    ttsAudioUrl: null,
    spokenAt: new Date('2026-05-01T10:00:10.250Z'),
    cooldownUntil: new Date('2026-05-01T10:00:40.000Z'),
    ...overrides,
  };
}

const HEADER: TranscriptHeader = {
  sessionId: 'sess-1',
  livekitRoomName: 'room-abc',
  actualStart: ANCHOR,
  actualEnd: new Date('2026-05-01T10:00:30.000Z'),
  participantNames: ['Alice', 'Bob'],
};

describe('buildTranscriptLines', () => {
  it('interleaves participant + moderator lines in start-time order', () => {
    const lines = buildTranscriptLines({
      anchor: ANCHOR,
      utterances: [
        makeUtterance({
          id: 'u-bob-late',
          participantDisplayName: 'Bob',
          text: 'second take',
          startTs: new Date('2026-05-01T10:00:15.000Z'),
          endTs: new Date('2026-05-01T10:00:17.000Z'),
        }),
        makeUtterance({
          id: 'u-alice-early',
          participantDisplayName: 'Alice',
          text: 'first take',
          startTs: new Date('2026-05-01T10:00:05.000Z'),
          endTs: new Date('2026-05-01T10:00:07.500Z'),
        }),
      ],
      moderatorDecisions: [makeDecision({})],
    });

    expect(lines.map((l) => ({ speaker: l.speaker, ms: l.startMs }))).toEqual([
      { speaker: 'Alice', ms: 5_000 },
      { speaker: 'Moderator', ms: 10_250 },
      { speaker: 'Bob', ms: 15_000 },
    ]);
  });

  it('skips non-final utterances and empty text', () => {
    const lines = buildTranscriptLines({
      anchor: ANCHOR,
      utterances: [
        makeUtterance({ id: 'u-interim', isFinal: false }),
        makeUtterance({ id: 'u-empty', text: '   ' }),
        makeUtterance({ id: 'u-real', text: 'kept' }),
      ],
      moderatorDecisions: [],
    });
    expect(lines).toHaveLength(1);
    expect(lines[0]?.text).toBe('kept');
  });

  it('skips decisions that were not executed or have no llm_output', () => {
    const lines = buildTranscriptLines({
      anchor: ANCHOR,
      utterances: [],
      moderatorDecisions: [
        makeDecision({ id: 'd-silent', wasExecuted: false, llmOutput: 'should not appear' }),
        makeDecision({ id: 'd-no-output', wasExecuted: true, llmOutput: null }),
        makeDecision({ id: 'd-real', wasExecuted: true, llmOutput: 'kept' }),
      ],
    });
    expect(lines).toHaveLength(1);
    expect(lines[0]?.speaker).toBe('Moderator');
    expect(lines[0]?.text).toBe('kept');
  });

  it('clamps lines that begin before the anchor to startMs=0', () => {
    const lines = buildTranscriptLines({
      anchor: ANCHOR,
      utterances: [
        makeUtterance({
          startTs: new Date('2026-05-01T09:59:59.500Z'),
          endTs: new Date('2026-05-01T10:00:00.250Z'),
          text: 'pre-anchor',
        }),
      ],
      moderatorDecisions: [],
    });
    expect(lines[0]?.startMs).toBe(0);
    // endMs is also clamped to 0, then bumped to startMs + 500 for a
    // minimum cue length.
    expect(lines[0]?.endMs).toBe(500);
  });

  it('scales moderator turn duration by word count, clamped between 1s and 10s', () => {
    const lines = buildTranscriptLines({
      anchor: ANCHOR,
      utterances: [],
      moderatorDecisions: [
        makeDecision({ id: 'd-short', llmOutput: 'Hi.', spokenAt: ANCHOR }),
        makeDecision({
          id: 'd-medium',
          // 10 words at 150 wpm = 4.0s.
          llmOutput: 'Bob, could you say a little more about that point please?',
          spokenAt: new Date('2026-05-01T10:00:15.000Z'),
        }),
        makeDecision({
          id: 'd-long',
          llmOutput: Array.from({ length: 60 }, () => 'word').join(' '),
          spokenAt: new Date('2026-05-01T10:00:30.000Z'),
        }),
      ],
    });
    const [short, medium, long] = lines;
    // Compare duration (endMs - startMs) rather than absolute endMs so
    // these assertions stay robust to the spokenAt offsets each line
    // carries.
    expect(short ? short.endMs - short.startMs : NaN).toBe(1_000); // clamped to floor
    expect(medium ? medium.endMs - medium.startMs : NaN).toBeGreaterThanOrEqual(3_500);
    expect(medium ? medium.endMs - medium.startMs : NaN).toBeLessThanOrEqual(4_500);
    expect(long ? long.endMs - long.startMs : NaN).toBe(10_000); // clamped to ceiling
  });
});

describe('formatTranscriptTxt', () => {
  it('renders header comments + one [HH:MM:SS] Speaker: text line per cue', () => {
    const lines: TranscriptLine[] = [
      { startMs: 5_000, endMs: 7_500, speaker: 'Alice', speakerKind: 'participant', text: 'hello' },
      {
        startMs: 10_250,
        endMs: 13_250,
        speaker: 'Moderator',
        speakerKind: 'moderator',
        text: 'Bob, your turn',
      },
    ];

    const out = formatTranscriptTxt(HEADER, lines);

    expect(out).toContain('# Verbio session transcript');
    expect(out).toContain('# Session: room-abc (sess-1)');
    expect(out).toContain('# Started: 2026-05-01T10:00:00.000Z');
    expect(out).toContain('# Participants: Alice, Bob');
    expect(out).toContain('[00:00:05] Alice: hello');
    expect(out).toContain('[00:00:10] Moderator: Bob, your turn');
    expect(out.endsWith('\n')).toBe(true);
  });

  it('flattens embedded newlines in a cue so each line is one row', () => {
    const lines: TranscriptLine[] = [
      {
        startMs: 0,
        endMs: 1_000,
        speaker: 'Alice',
        speakerKind: 'participant',
        text: 'first part\nsecond part',
      },
    ];
    const out = formatTranscriptTxt(HEADER, lines);
    expect(out).toContain('[00:00:00] Alice: first part second part');
  });

  it('omits a trailing newline for an empty body', () => {
    const out = formatTranscriptTxt(HEADER, []);
    expect(out.endsWith('\n')).toBe(false);
    expect(out).toContain('# Verbio session transcript');
  });
});

describe('formatTranscriptVtt', () => {
  it('opens with WEBVTT + NOTE blocks, then numbered cues with voice tags', () => {
    const lines: TranscriptLine[] = [
      {
        startMs: 5_120,
        endMs: 7_500,
        speaker: 'Alice',
        speakerKind: 'participant',
        text: 'hi everyone',
      },
      {
        startMs: 10_250,
        endMs: 13_250,
        speaker: 'Moderator',
        speakerKind: 'moderator',
        text: 'Bob, your turn',
      },
    ];

    const out = formatTranscriptVtt(HEADER, lines);

    expect(out.startsWith('WEBVTT')).toBe(true);
    expect(out).toContain('NOTE Verbio session sess-1 (room-abc)');
    expect(out).toContain('00:00:05.120 --> 00:00:07.500');
    expect(out).toContain('<v Alice>hi everyone');
    expect(out).toContain('00:00:10.250 --> 00:00:13.250');
    expect(out).toContain('<v Moderator>Bob, your turn');
  });

  it('escapes & and < in cue body and strips angle brackets from the voice tag', () => {
    const lines: TranscriptLine[] = [
      {
        startMs: 0,
        endMs: 1_000,
        speaker: 'Al<ice>',
        speakerKind: 'participant',
        text: 'fish & chips < pizza',
      },
    ];

    const out = formatTranscriptVtt(HEADER, lines);

    expect(out).toContain('<v Alice>fish &amp; chips &lt; pizza');
    expect(out).not.toContain('Al<ice>');
  });

  it('collapses embedded newlines in cue body to a single space', () => {
    const lines: TranscriptLine[] = [
      {
        startMs: 0,
        endMs: 1_000,
        speaker: 'Alice',
        speakerKind: 'participant',
        text: 'line one\n  line two',
      },
    ];
    const out = formatTranscriptVtt(HEADER, lines);
    expect(out).toContain('<v Alice>line one line two');
  });

  it('formats hour:minute:second.millis precisely', () => {
    const lines: TranscriptLine[] = [
      {
        startMs: 60_000 + 5_007,
        endMs: 65_000 + 7,
        speaker: 'Alice',
        speakerKind: 'participant',
        text: 'one minute in',
      },
    ];
    const out = formatTranscriptVtt(HEADER, lines);
    expect(out).toContain('00:01:05.007 --> 00:01:05.007');
  });
});
