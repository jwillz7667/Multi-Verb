'use client';

/**
 * Replay filters bar — narrows the timeline + decision-detail surfaces
 * to the slice of decisions a researcher cares about (brief §11.2).
 *
 * Four facets, each independent:
 *
 *   - action: stay_silent / prompt / redirect / summary / whisper / …
 *   - participant: target_participant_id (decisions with no target —
 *     e.g., a global stay_silent — drop out when this is active),
 *   - rule: triggering_rule (decisions without a triggering rule drop
 *     out when this is active),
 *   - source: rules_engine / researcher_manual / researcher_whisper.
 *
 * Within a facet the selected values are OR'd; across facets they're
 * AND'd. An empty set in a facet means "all of this facet" — so the
 * default state is "show everything".
 *
 * Only values that actually appear in the session's decisions show up
 * as chips. A session with no researcher overrides has no
 * `researcher_*` source chip; a session with no `unheard_participant`
 * firings has no `unheard_participant` rule chip. Cleaner than
 * presenting researchers with every possible enum value and letting
 * them click into dead ends.
 *
 * The bar is purely presentational. The shell owns `ReplayFiltersState`
 * and feeds the derived `filteredDecisions` to the timeline + the
 * detail panel; this component just toggles chips and emits.
 */

import { useMemo } from 'react';

import type { ReplayDecisionDTO, ReplayParticipantDTO } from './replay-types';

export interface ReplayFiltersState {
  actions: ReadonlySet<string>;
  participants: ReadonlySet<string>;
  rules: ReadonlySet<string>;
  sources: ReadonlySet<string>;
}

export const EMPTY_FILTERS: ReplayFiltersState = {
  actions: new Set<string>(),
  participants: new Set<string>(),
  rules: new Set<string>(),
  sources: new Set<string>(),
};

export function isAnyFilterActive(filters: ReplayFiltersState): boolean {
  return (
    filters.actions.size > 0 ||
    filters.participants.size > 0 ||
    filters.rules.size > 0 ||
    filters.sources.size > 0
  );
}

export function applyDecisionFilters(
  decisions: ReplayDecisionDTO[],
  filters: ReplayFiltersState,
): ReplayDecisionDTO[] {
  if (!isAnyFilterActive(filters)) return decisions;
  return decisions.filter((d) => decisionMatchesFilters(d, filters));
}

function decisionMatchesFilters(d: ReplayDecisionDTO, f: ReplayFiltersState): boolean {
  if (f.actions.size > 0 && !f.actions.has(d.action)) return false;
  if (f.participants.size > 0) {
    // A null target can't match any selected participant — that
    // includes most global `stay_silent` decisions. The researcher
    // asked for a participant-scoped view; honor that.
    if (d.target_participant_id === null) return false;
    if (!f.participants.has(d.target_participant_id)) return false;
  }
  if (f.rules.size > 0) {
    if (d.triggering_rule === null) return false;
    if (!f.rules.has(d.triggering_rule)) return false;
  }
  if (f.sources.size > 0 && !f.sources.has(d.source)) return false;
  return true;
}

interface Props {
  decisions: ReplayDecisionDTO[];
  participants: ReplayParticipantDTO[];
  filters: ReplayFiltersState;
  filteredCount: number;
  onChange: (next: ReplayFiltersState) => void;
}

interface ChipOption {
  value: string;
  label: string;
}

interface FacetOptions {
  actions: ChipOption[];
  participants: ChipOption[];
  rules: ChipOption[];
  sources: ChipOption[];
}

export function ReplayFilters({
  decisions,
  participants,
  filters,
  filteredCount,
  onChange,
}: Props): React.ReactElement {
  const options = useMemo<FacetOptions>(
    () => deriveFacetOptions(decisions, participants),
    [decisions, participants],
  );

  const toggle = (facet: keyof ReplayFiltersState, value: string): void => {
    const current = filters[facet];
    const next = new Set(current);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    onChange({ ...filters, [facet]: next });
  };

  const clearAll = (): void => {
    onChange(EMPTY_FILTERS);
  };

  const anyActive = isAnyFilterActive(filters);

  return (
    <section
      className="border-border-default bg-surface-primary flex flex-col gap-3 rounded-lg border px-6 py-4"
      data-testid="replay-filters-slot"
    >
      <div className="flex items-center justify-between">
        <span className="text-text-tertiary text-xs uppercase tracking-wide">Filters</span>
        <div className="flex items-center gap-3">
          <span className="text-text-tertiary text-xs" data-testid="replay-filters-count">
            {anyActive
              ? `${filteredCount.toString()} of ${decisions.length.toString()} decisions`
              : `${decisions.length.toString()} decision${decisions.length === 1 ? '' : 's'}`}
          </span>
          {anyActive && (
            <button
              type="button"
              onClick={clearAll}
              className="text-text-secondary text-xs underline hover:text-text-primary"
              data-testid="replay-filters-clear"
            >
              Clear filters
            </button>
          )}
        </div>
      </div>

      {decisions.length === 0 ? (
        <p className="text-text-tertiary text-xs italic">
          No decisions in this window — nothing to filter.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          <FilterChipGroup
            label="Action"
            testid="replay-filter-actions"
            options={options.actions}
            selected={filters.actions}
            onToggle={(v): void => {
              toggle('actions', v);
            }}
          />
          <FilterChipGroup
            label="Participant"
            testid="replay-filter-participants"
            options={options.participants}
            selected={filters.participants}
            onToggle={(v): void => {
              toggle('participants', v);
            }}
            emptyCopy="No participant-targeted decisions"
          />
          <FilterChipGroup
            label="Rule"
            testid="replay-filter-rules"
            options={options.rules}
            selected={filters.rules}
            onToggle={(v): void => {
              toggle('rules', v);
            }}
            emptyCopy="No rule-triggered decisions"
          />
          <FilterChipGroup
            label="Source"
            testid="replay-filter-sources"
            options={options.sources}
            selected={filters.sources}
            onToggle={(v): void => {
              toggle('sources', v);
            }}
          />
        </div>
      )}
    </section>
  );
}

function FilterChipGroup({
  label,
  testid,
  options,
  selected,
  onToggle,
  emptyCopy,
}: {
  label: string;
  testid: string;
  options: ChipOption[];
  selected: ReadonlySet<string>;
  onToggle: (value: string) => void;
  emptyCopy?: string;
}): React.ReactElement {
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid={testid}>
      <span className="text-text-secondary w-20 shrink-0 text-xs font-medium">{label}</span>
      {options.length === 0 ? (
        <span className="text-text-tertiary text-xs italic">{emptyCopy ?? '—'}</span>
      ) : (
        options.map((opt) => {
          const isOn = selected.has(opt.value);
          return (
            <button
              key={opt.value}
              type="button"
              onClick={(): void => {
                onToggle(opt.value);
              }}
              aria-pressed={isOn}
              className={`rounded-full border px-2 py-0.5 text-xs ${
                isOn
                  ? 'border-accent bg-accent-bg text-accent'
                  : 'border-border-default text-text-secondary hover:border-border-strong hover:text-text-primary'
              }`}
              data-testid={`${testid}-chip-${opt.value}`}
            >
              {opt.label}
            </button>
          );
        })
      )}
    </div>
  );
}

function deriveFacetOptions(
  decisions: ReplayDecisionDTO[],
  participants: ReplayParticipantDTO[],
): FacetOptions {
  // Distinct value collection. Stable order keeps the chip layout from
  // jittering as the bootstrap window shifts.
  const actionSet = new Set<string>();
  const participantSet = new Set<string>();
  const ruleSet = new Set<string>();
  const sourceSet = new Set<string>();
  for (const d of decisions) {
    actionSet.add(d.action);
    if (d.target_participant_id !== null) participantSet.add(d.target_participant_id);
    if (d.triggering_rule !== null) ruleSet.add(d.triggering_rule);
    sourceSet.add(d.source);
  }

  const participantById = new Map<string, ReplayParticipantDTO>();
  for (const p of participants) participantById.set(p.id, p);

  const actions: ChipOption[] = [...actionSet].sort().map((value) => ({
    value,
    label: value,
  }));
  const participantOptions: ChipOption[] = [...participantSet]
    .map<ChipOption>((id) => ({
      value: id,
      label: participantById.get(id)?.display_name ?? id,
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
  const rules: ChipOption[] = [...ruleSet].sort().map((value) => ({ value, label: value }));
  const sources: ChipOption[] = [...sourceSet].sort().map((value) => ({
    value,
    label: sourceLabel(value),
  }));

  return { actions, participants: participantOptions, rules, sources };
}

function sourceLabel(source: string): string {
  if (source === 'rules_engine') return 'engine';
  if (source === 'researcher_manual') return 'researcher';
  if (source === 'researcher_whisper') return 'whisper';
  return source;
}
