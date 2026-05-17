/**
 * Tests for the replay filter bar + its pure helpers.
 *
 * Coverage:
 *
 *   - `applyDecisionFilters` is conjunction across facets, disjunction
 *     within, and a no-op when every facet is empty.
 *   - A null `target_participant_id` / null `triggering_rule` is
 *     excluded when those facets are active (no implicit pass-through).
 *   - The chip grid only enumerates values that actually appear in the
 *     decisions — no dead chips.
 *   - Toggling a chip emits an updated state with the value flipped in
 *     the right facet.
 *   - "Clear filters" only renders when something is active, and emits
 *     EMPTY_FILTERS.
 *   - The filtered-count copy reflects the live count.
 *   - The shell drops `selectedDecisionId` when filters exclude it,
 *     covered alongside the shell smoke tests.
 */

import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  applyDecisionFilters,
  EMPTY_FILTERS,
  isAnyFilterActive,
  ReplayFilters,
  type ReplayFiltersState,
} from './replay-filters';

import type { ReplayDecisionDTO, ReplayParticipantDTO } from './replay-types';

const ALICE: ReplayParticipantDTO = {
  id: 'p-alice',
  display_name: 'Alice',
  livekit_identity: 'alice-001',
  role: 'participant',
  joined_at: '2026-05-01T10:00:00.000Z',
  left_at: null,
};

const BOB: ReplayParticipantDTO = {
  id: 'p-bob',
  display_name: 'Bob',
  livekit_identity: 'bob-002',
  role: 'participant',
  joined_at: '2026-05-01T10:00:00.000Z',
  left_at: null,
};

function makeDecision(overrides: Partial<ReplayDecisionDTO>): ReplayDecisionDTO {
  return {
    id: 'dec-1',
    tick_id: 0,
    ts: '2026-05-01T10:00:00.000Z',
    action: 'stay_silent',
    target_participant_id: null,
    source: 'rules_engine',
    triggering_rule: null,
    researcher_id: null,
    researcher_hint: null,
    reason_codes: [],
    reason_human: '',
    confidence: null,
    suppressed_by: [],
    was_executed: false,
    llm_prompt: null,
    llm_output: null,
    tts_audio_url: null,
    spoken_at: null,
    cooldown_until: '2026-05-01T10:00:00.000Z',
    ...overrides,
  };
}

describe('applyDecisionFilters', () => {
  const DECISIONS: ReplayDecisionDTO[] = [
    makeDecision({
      id: 'd-silent-global',
      action: 'stay_silent',
      source: 'rules_engine',
      triggering_rule: null,
      target_participant_id: null,
    }),
    makeDecision({
      id: 'd-prompt-alice-unheard',
      action: 'prompt',
      source: 'rules_engine',
      triggering_rule: 'unheard_participant',
      target_participant_id: ALICE.id,
    }),
    makeDecision({
      id: 'd-redirect-bob-imbalance',
      action: 'redirect',
      source: 'rules_engine',
      triggering_rule: 'speaker_imbalance',
      target_participant_id: BOB.id,
    }),
    makeDecision({
      id: 'd-whisper-alice',
      action: 'whisper',
      source: 'researcher_whisper',
      triggering_rule: null,
      target_participant_id: ALICE.id,
    }),
    makeDecision({
      id: 'd-force-prompt-bob',
      action: 'prompt',
      source: 'researcher_manual',
      triggering_rule: null,
      target_participant_id: BOB.id,
    }),
  ];

  it('returns the input unchanged when every facet is empty', () => {
    const out = applyDecisionFilters(DECISIONS, EMPTY_FILTERS);
    expect(out).toBe(DECISIONS);
  });

  it('filters by action (disjunction within facet)', () => {
    const out = applyDecisionFilters(DECISIONS, {
      ...EMPTY_FILTERS,
      actions: new Set(['prompt', 'redirect']),
    });
    expect(out.map((d) => d.id)).toEqual([
      'd-prompt-alice-unheard',
      'd-redirect-bob-imbalance',
      'd-force-prompt-bob',
    ]);
  });

  it('excludes null target_participant_id when a participant filter is active', () => {
    const out = applyDecisionFilters(DECISIONS, {
      ...EMPTY_FILTERS,
      participants: new Set([ALICE.id]),
    });
    expect(out.map((d) => d.id)).toEqual(['d-prompt-alice-unheard', 'd-whisper-alice']);
  });

  it('excludes null triggering_rule when a rule filter is active', () => {
    const out = applyDecisionFilters(DECISIONS, {
      ...EMPTY_FILTERS,
      rules: new Set(['unheard_participant']),
    });
    expect(out.map((d) => d.id)).toEqual(['d-prompt-alice-unheard']);
  });

  it('conjoins across facets (action AND source)', () => {
    const out = applyDecisionFilters(DECISIONS, {
      ...EMPTY_FILTERS,
      actions: new Set(['prompt']),
      sources: new Set(['researcher_manual']),
    });
    expect(out.map((d) => d.id)).toEqual(['d-force-prompt-bob']);
  });

  it('returns an empty array when filters exclude everything', () => {
    const out = applyDecisionFilters(DECISIONS, {
      ...EMPTY_FILTERS,
      actions: new Set(['summary']),
    });
    expect(out).toEqual([]);
  });
});

describe('isAnyFilterActive', () => {
  it('returns false for the EMPTY_FILTERS sentinel', () => {
    expect(isAnyFilterActive(EMPTY_FILTERS)).toBe(false);
  });
  it('returns true when any facet has a selection', () => {
    expect(isAnyFilterActive({ ...EMPTY_FILTERS, actions: new Set(['prompt']) })).toBe(true);
  });
});

describe('<ReplayFilters />', () => {
  const DECISIONS: ReplayDecisionDTO[] = [
    makeDecision({
      id: 'd-1',
      action: 'stay_silent',
      source: 'rules_engine',
      triggering_rule: null,
      target_participant_id: null,
    }),
    makeDecision({
      id: 'd-2',
      action: 'prompt',
      source: 'rules_engine',
      triggering_rule: 'unheard_participant',
      target_participant_id: ALICE.id,
    }),
    makeDecision({
      id: 'd-3',
      action: 'whisper',
      source: 'researcher_whisper',
      triggering_rule: null,
      target_participant_id: BOB.id,
    }),
  ];

  it('renders one chip per distinct value present in decisions, with participant display names', () => {
    render(
      <ReplayFilters
        decisions={DECISIONS}
        participants={[ALICE, BOB]}
        filters={EMPTY_FILTERS}
        filteredCount={DECISIONS.length}
        onChange={vi.fn()}
      />,
    );

    const actions = screen.getByTestId('replay-filter-actions');
    expect(within(actions).getByTestId('replay-filter-actions-chip-prompt')).toBeInTheDocument();
    expect(
      within(actions).getByTestId('replay-filter-actions-chip-stay_silent'),
    ).toBeInTheDocument();
    expect(within(actions).getByTestId('replay-filter-actions-chip-whisper')).toBeInTheDocument();

    const participants = screen.getByTestId('replay-filter-participants');
    // display_name rendered, not the id.
    expect(
      within(participants).getByTestId(`replay-filter-participants-chip-${ALICE.id}`),
    ).toHaveTextContent('Alice');
    expect(
      within(participants).getByTestId(`replay-filter-participants-chip-${BOB.id}`),
    ).toHaveTextContent('Bob');

    const rules = screen.getByTestId('replay-filter-rules');
    expect(
      within(rules).getByTestId('replay-filter-rules-chip-unheard_participant'),
    ).toBeInTheDocument();
    // No `speaker_imbalance` chip — it never appears in DECISIONS.
    expect(
      within(rules).queryByTestId('replay-filter-rules-chip-speaker_imbalance'),
    ).not.toBeInTheDocument();

    const sources = screen.getByTestId('replay-filter-sources');
    expect(
      within(sources).getByTestId('replay-filter-sources-chip-rules_engine'),
    ).toHaveTextContent('engine');
    expect(
      within(sources).getByTestId('replay-filter-sources-chip-researcher_whisper'),
    ).toHaveTextContent('whisper');
  });

  it('emits an updated state when a chip is toggled on', () => {
    const onChange = vi.fn();

    render(
      <ReplayFilters
        decisions={DECISIONS}
        participants={[ALICE, BOB]}
        filters={EMPTY_FILTERS}
        filteredCount={DECISIONS.length}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByTestId('replay-filter-actions-chip-prompt'));

    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0]?.[0] as ReplayFiltersState;
    expect([...next.actions]).toEqual(['prompt']);
    expect(next.participants.size).toBe(0);
  });

  it('emits a state with the chip removed when toggling an active chip off', () => {
    const onChange = vi.fn();

    render(
      <ReplayFilters
        decisions={DECISIONS}
        participants={[ALICE, BOB]}
        filters={{ ...EMPTY_FILTERS, actions: new Set(['prompt']) }}
        filteredCount={1}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByTestId('replay-filter-actions-chip-prompt'));

    const next = onChange.mock.calls[0]?.[0] as ReplayFiltersState;
    expect(next.actions.size).toBe(0);
  });

  it('hides "Clear filters" when nothing is active and shows it once a filter is set', () => {
    const { rerender } = render(
      <ReplayFilters
        decisions={DECISIONS}
        participants={[ALICE, BOB]}
        filters={EMPTY_FILTERS}
        filteredCount={DECISIONS.length}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('replay-filters-clear')).not.toBeInTheDocument();

    rerender(
      <ReplayFilters
        decisions={DECISIONS}
        participants={[ALICE, BOB]}
        filters={{ ...EMPTY_FILTERS, sources: new Set(['rules_engine']) }}
        filteredCount={2}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('replay-filters-clear')).toBeInTheDocument();
  });

  it('emits EMPTY_FILTERS when "Clear filters" is clicked', () => {
    const onChange = vi.fn();

    render(
      <ReplayFilters
        decisions={DECISIONS}
        participants={[ALICE, BOB]}
        filters={{ ...EMPTY_FILTERS, actions: new Set(['prompt']) }}
        filteredCount={1}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByTestId('replay-filters-clear'));

    const next = onChange.mock.calls[0]?.[0] as ReplayFiltersState;
    expect(isAnyFilterActive(next)).toBe(false);
  });

  it('shows "X of Y decisions" when a filter is active and "Y decisions" otherwise', () => {
    const { rerender } = render(
      <ReplayFilters
        decisions={DECISIONS}
        participants={[ALICE, BOB]}
        filters={EMPTY_FILTERS}
        filteredCount={DECISIONS.length}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('replay-filters-count')).toHaveTextContent('3 decisions');

    rerender(
      <ReplayFilters
        decisions={DECISIONS}
        participants={[ALICE, BOB]}
        filters={{ ...EMPTY_FILTERS, actions: new Set(['prompt']) }}
        filteredCount={1}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('replay-filters-count')).toHaveTextContent('1 of 3 decisions');
  });

  it('renders an empty-state message when there are no decisions to filter', () => {
    render(
      <ReplayFilters
        decisions={[]}
        participants={[ALICE, BOB]}
        filters={EMPTY_FILTERS}
        filteredCount={0}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/nothing to filter/i)).toBeInTheDocument();
    expect(screen.queryByTestId('replay-filter-actions')).not.toBeInTheDocument();
  });

  it('sets aria-pressed on chips reflecting their selected state', () => {
    render(
      <ReplayFilters
        decisions={DECISIONS}
        participants={[ALICE, BOB]}
        filters={{ ...EMPTY_FILTERS, actions: new Set(['prompt']) }}
        filteredCount={1}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('replay-filter-actions-chip-prompt')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByTestId('replay-filter-actions-chip-stay_silent')).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });
});
