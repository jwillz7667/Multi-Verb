/**
 * Tests for the decision-log CSV formatter.
 *
 * Coverage:
 *   - Header matches DECISION_LOG_COLUMNS in declared order (downstream
 *     analysis scripts depend on column position).
 *   - Each row maps the schema fields one-to-one in the right slot.
 *   - Target participant name is joined from the participants lookup.
 *   - Null/optional fields render as empty cells, not literal "null".
 *   - Multi-value fields (reason_codes, suppressed_by) join with `; `.
 *   - RFC 4180 quoting: commas, quotes, CR, LF in cell values are
 *     wrapped in quotes with internal `"` doubled.
 *   - Trailing newline so concatenation downstream stays clean.
 */
import { describe, expect, it } from 'vitest';

import type { DecisionRow, ParticipantRow } from '@/features/sessions';

import {
  DECISION_LOG_COLUMNS,
  formatDecisionLogCsv,
  formatDecisionLogHeader,
  formatDecisionLogRow,
} from './decision-log';

function makeParticipant(overrides: Partial<ParticipantRow>): ParticipantRow {
  return {
    id: 'p-default',
    sessionId: 'sess-1',
    displayName: 'Default',
    role: 'participant',
    joinedAt: new Date('2026-05-01T10:00:00.000Z'),
    leftAt: null,
    livekitIdentity: 'default-001',
    ...overrides,
  };
}

function makeDecision(overrides: Partial<DecisionRow>): DecisionRow {
  return {
    id: 'd-default',
    sessionId: 'sess-1',
    tickId: 42n,
    ts: new Date('2026-05-01T10:00:10.000Z'),
    action: 'prompt',
    targetParticipantId: 'p-alice',
    source: 'rules_engine',
    triggeringRule: 'unheard_participant',
    researcherId: null,
    researcherHint: null,
    reasonCodes: ['unheard_60s'],
    reasonHuman: 'Alice has not spoken for 60s',
    confidence: 0.81,
    suppressedBy: [],
    wasExecuted: true,
    llmPrompt: null,
    llmOutput: 'Alice, what is your take?',
    ttsAudioUrl: null,
    spokenAt: new Date('2026-05-01T10:00:10.250Z'),
    cooldownUntil: new Date('2026-05-01T10:00:40.000Z'),
    ...overrides,
  };
}

const PARTICIPANTS_BY_ID = new Map<string, ParticipantRow>([
  ['p-alice', makeParticipant({ id: 'p-alice', displayName: 'Alice' })],
  ['p-bob', makeParticipant({ id: 'p-bob', displayName: 'Bob' })],
]);

describe('formatDecisionLogHeader', () => {
  it('emits the column names in declared order', () => {
    expect(formatDecisionLogHeader()).toBe(DECISION_LOG_COLUMNS.join(','));
  });

  it('starts with ts and ends with cooldown_until — the column order downstream tooling depends on', () => {
    const cols = DECISION_LOG_COLUMNS;
    expect(cols[0]).toBe('ts');
    expect(cols[cols.length - 1]).toBe('cooldown_until');
  });
});

describe('formatDecisionLogRow', () => {
  it('maps schema fields onto cells in the column order', () => {
    // Override the default llmOutput / reasonHuman to comma-free
    // values so the naive split-on-comma matches column boundaries
    // exactly. Quoting behavior for comma-bearing fields is exercised
    // in its own test below.
    const out = formatDecisionLogRow(
      makeDecision({
        llmOutput: 'Alice your take',
        reasonHuman: 'Alice silent 60s',
      }),
      PARTICIPANTS_BY_ID,
    );
    const cells = out.split(',');
    // Header column count drives the cell count — even with empty
    // optional fields, every row must have the same column arity.
    expect(cells).toHaveLength(DECISION_LOG_COLUMNS.length);
    expect(cells[0]).toBe('2026-05-01T10:00:10.000Z');
    expect(cells[1]).toBe('42');
    expect(cells[2]).toBe('prompt');
    expect(cells[3]).toBe('p-alice');
    expect(cells[4]).toBe('Alice');
    expect(cells[5]).toBe('rules_engine');
    expect(cells[6]).toBe('unheard_participant');
    // researcher_id, researcher_hint are empty cells (null).
    expect(cells[7]).toBe('');
    expect(cells[8]).toBe('');
    expect(cells[9]).toBe('unheard_60s');
    expect(cells[10]).toBe('Alice silent 60s');
    expect(cells[11]).toBe('0.81');
    expect(cells[12]).toBe('true');
    expect(cells[13]).toBe(''); // suppressed_by empty
    expect(cells[14]).toBe('Alice your take');
    expect(cells[15]).toBe('2026-05-01T10:00:10.250Z');
    expect(cells[16]).toBe('2026-05-01T10:00:40.000Z');
  });

  it('joins multi-value fields with "; " separator', () => {
    const out = formatDecisionLogRow(
      makeDecision({
        reasonCodes: ['unheard_60s', 'low_engagement'],
        suppressedBy: ['cooldown', 'quietness_budget'],
      }),
      PARTICIPANTS_BY_ID,
    );
    expect(out).toContain('unheard_60s; low_engagement');
    expect(out).toContain('cooldown; quietness_budget');
  });

  it('renders empty string for null target participant', () => {
    const out = formatDecisionLogRow(
      makeDecision({ targetParticipantId: null }),
      PARTICIPANTS_BY_ID,
    );
    const cells = out.split(',');
    // target_participant_id (col 3) and target_participant_name (col 4)
    // are both empty.
    expect(cells[3]).toBe('');
    expect(cells[4]).toBe('');
  });

  it('renders empty target name when the id has no matching participant', () => {
    const out = formatDecisionLogRow(
      makeDecision({ targetParticipantId: 'p-unknown' }),
      PARTICIPANTS_BY_ID,
    );
    const cells = out.split(',');
    expect(cells[3]).toBe('p-unknown');
    expect(cells[4]).toBe('');
  });

  it('renders confidence empty when null', () => {
    const out = formatDecisionLogRow(makeDecision({ confidence: null }), PARTICIPANTS_BY_ID);
    const cells = out.split(',');
    expect(cells[11]).toBe('');
  });

  it('renders was_executed as "true"/"false" literals', () => {
    const yes = formatDecisionLogRow(makeDecision({ wasExecuted: true }), PARTICIPANTS_BY_ID);
    const no = formatDecisionLogRow(makeDecision({ wasExecuted: false }), PARTICIPANTS_BY_ID);
    expect(yes.split(',')[12]).toBe('true');
    expect(no.split(',')[12]).toBe('false');
  });

  it('renders spoken_at empty when null and cooldown_until as ISO (always set on persistence)', () => {
    const out = formatDecisionLogRow(
      makeDecision({ spokenAt: null, wasExecuted: false, llmOutput: null }),
      PARTICIPANTS_BY_ID,
    );
    const cells = out.split(',');
    expect(cells[14]).toBe(''); // llm_output
    expect(cells[15]).toBe(''); // spoken_at
    // cooldown_until is non-null in the schema — every decision sets
    // it, including stay_silent rows (cooldownUntil = decision.ts).
    expect(cells[16]).toBe('2026-05-01T10:00:40.000Z');
  });

  it('quotes cells containing commas and doubles internal quotes per RFC 4180', () => {
    const out = formatDecisionLogRow(
      makeDecision({
        reasonHuman: 'Alice, who said "hi", interrupted',
        llmOutput: 'commaful, line',
      }),
      PARTICIPANTS_BY_ID,
    );
    expect(out).toContain('"Alice, who said ""hi"", interrupted"');
    expect(out).toContain('"commaful, line"');
  });

  it('quotes cells containing CR or LF so the row stays on one line', () => {
    const out = formatDecisionLogRow(
      makeDecision({ reasonHuman: 'line one\nline two' }),
      PARTICIPANTS_BY_ID,
    );
    expect(out).toContain('"line one\nline two"');
  });
});

describe('formatDecisionLogCsv', () => {
  it('starts with the header row and ends with a trailing newline', () => {
    const out = formatDecisionLogCsv([makeDecision({})], PARTICIPANTS_BY_ID);
    expect(out.startsWith(formatDecisionLogHeader() + '\n')).toBe(true);
    expect(out.endsWith('\n')).toBe(true);
  });

  it('emits one row per decision plus the header', () => {
    const out = formatDecisionLogCsv(
      [makeDecision({ id: 'd-1' }), makeDecision({ id: 'd-2' }), makeDecision({ id: 'd-3' })],
      PARTICIPANTS_BY_ID,
    );
    // Header + 3 rows + trailing newline → 4 newlines after trim.
    expect(out.trimEnd().split('\n')).toHaveLength(4);
  });

  it('returns just the header (plus trailing newline) when there are no decisions', () => {
    const out = formatDecisionLogCsv([], PARTICIPANTS_BY_ID);
    expect(out).toBe(formatDecisionLogHeader() + '\n');
  });
});
