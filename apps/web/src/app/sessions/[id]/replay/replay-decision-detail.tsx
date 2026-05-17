'use client';

/**
 * Replay decision detail panel — answers "why did the moderator say
 * that?" and "why didn't it speak here?" (brief §3, §11.2).
 *
 * Two halves:
 *
 *   1. Decision card. Renders from the bootstrap decision row —
 *      action, source (rules_engine / researcher_*), target
 *      participant, triggering rule, reason codes, reason_human,
 *      confidence, suppressed_by, executed/not, and the LLM
 *      prompt+output when the decision actually went through the
 *      mouth.
 *   2. Per-rule evaluations table, fetched on demand from
 *      `/api/sessions/[id]/decisions/[decisionId]/evaluations`. Each
 *      row shows whether the rule fired, the suppressed_reason if
 *      any, and the rule version that was in play. The table is the
 *      audit trail brief §3 promises — every rule, every tick, with
 *      its verdict explicit.
 *
 * The fetch is keyed by decisionId so flipping between markers reads
 * cached responses; results stay until the panel unmounts.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { ReplayDecisionDTO, ReplayParticipantDTO } from './replay-types';

interface Props {
  sessionId: string;
  decisions: ReplayDecisionDTO[];
  participants: ReplayParticipantDTO[];
  selectedDecisionId: string | null;
}

interface EvaluationDTO {
  id: string;
  ruleName: string;
  ruleVersion: string;
  fired: boolean;
  suppressedReason: string | null;
  confidence: number;
}

interface EvaluationsResponse {
  decision_id: string;
  evaluations: EvaluationDTO[];
}

type EvaluationsState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; rows: EvaluationDTO[] }
  | { kind: 'error'; code: 'not_found' | 'unauthorized' | 'network' };

export function ReplayDecisionDetail({
  sessionId,
  decisions,
  participants,
  selectedDecisionId,
}: Props): React.ReactElement {
  const decisionsById = useMemo(() => {
    const map = new Map<string, ReplayDecisionDTO>();
    for (const d of decisions) map.set(d.id, d);
    return map;
  }, [decisions]);

  const participantsById = useMemo(() => {
    const map = new Map<string, ReplayParticipantDTO>();
    for (const p of participants) map.set(p.id, p);
    return map;
  }, [participants]);

  // Cache evaluations across decision flips so the panel doesn't
  // re-fetch the same decision twice in a row when a researcher
  // ping-pongs between two markers.
  const [evaluationsCache, setEvaluationsCache] = useState<ReadonlyMap<string, EvaluationDTO[]>>(
    new Map(),
  );
  const [evaluationsState, setEvaluationsState] = useState<EvaluationsState>({ kind: 'idle' });

  // Negative cache: 404s on decision id (e.g., row deleted mid-replay)
  // so we don't loop on the failure.
  const negativeCacheRef = useRef<Set<string>>(new Set());
  // Inflight dedupe — multiple useEffect runs for the same decision
  // shouldn't issue two concurrent fetches.
  const inflightRef = useRef<Set<string>>(new Set());

  const selectedDecision =
    selectedDecisionId === null ? null : (decisionsById.get(selectedDecisionId) ?? null);

  const fetchEvaluations = useCallback(
    async (decisionId: string): Promise<void> => {
      if (inflightRef.current.has(decisionId)) return;
      if (negativeCacheRef.current.has(decisionId)) return;
      inflightRef.current.add(decisionId);
      setEvaluationsState({ kind: 'loading' });
      try {
        const res = await fetch(`/api/sessions/${sessionId}/decisions/${decisionId}/evaluations`);
        if (res.status === 200) {
          const body = (await res.json()) as EvaluationsResponse;
          setEvaluationsCache((prev) => {
            const next = new Map(prev);
            next.set(decisionId, body.evaluations);
            return next;
          });
          setEvaluationsState({ kind: 'ready', rows: body.evaluations });
          return;
        }
        if (res.status === 404) {
          negativeCacheRef.current.add(decisionId);
          setEvaluationsState({ kind: 'error', code: 'not_found' });
          return;
        }
        if (res.status === 401) {
          setEvaluationsState({ kind: 'error', code: 'unauthorized' });
          return;
        }
        setEvaluationsState({ kind: 'error', code: 'network' });
      } catch {
        setEvaluationsState({ kind: 'error', code: 'network' });
      } finally {
        inflightRef.current.delete(decisionId);
      }
    },
    [sessionId],
  );

  useEffect(() => {
    if (selectedDecisionId === null) {
      setEvaluationsState({ kind: 'idle' });
      return;
    }
    const cached = evaluationsCache.get(selectedDecisionId);
    if (cached !== undefined) {
      setEvaluationsState({ kind: 'ready', rows: cached });
      return;
    }
    if (negativeCacheRef.current.has(selectedDecisionId)) {
      setEvaluationsState({ kind: 'error', code: 'not_found' });
      return;
    }
    void fetchEvaluations(selectedDecisionId);
  }, [selectedDecisionId, evaluationsCache, fetchEvaluations]);

  return (
    <section
      className="border-border-default bg-surface-primary flex min-h-[20rem] flex-col gap-3 rounded-lg border p-6"
      data-testid="replay-decision-slot"
    >
      <header className="flex items-center justify-between">
        <h2 className="text-text-primary text-sm font-medium uppercase tracking-wide">
          Decision detail
        </h2>
        {selectedDecision !== null && (
          <span className="text-text-tertiary font-mono text-xs" data-testid="replay-decision-ts">
            tick {selectedDecision.tick_id} · {selectedDecision.ts}
          </span>
        )}
      </header>

      {selectedDecision === null ? (
        <p className="text-text-secondary mt-2 text-sm" data-testid="replay-decision-empty">
          Select a marker on the timeline to inspect a decision.
        </p>
      ) : (
        <DecisionCard
          decision={selectedDecision}
          participantsById={participantsById}
          evaluationsState={evaluationsState}
        />
      )}
    </section>
  );
}

function DecisionCard({
  decision,
  participantsById,
  evaluationsState,
}: {
  decision: ReplayDecisionDTO;
  participantsById: Map<string, ReplayParticipantDTO>;
  evaluationsState: EvaluationsState;
}): React.ReactElement {
  const targetName =
    decision.target_participant_id === null
      ? null
      : (participantsById.get(decision.target_participant_id)?.display_name ??
        decision.target_participant_id);

  return (
    <div className="flex flex-col gap-4" data-testid="replay-decision-card">
      <div className="flex flex-wrap items-center gap-2">
        <ActionPill action={decision.action} executed={decision.was_executed} />
        <SourcePill source={decision.source} />
        {targetName !== null && (
          <span
            className="border-border-default text-text-secondary rounded-full border px-2 py-0.5 text-xs"
            data-testid="replay-decision-target"
          >
            → {targetName}
          </span>
        )}
        {decision.triggering_rule !== null && (
          <span className="text-text-tertiary font-mono text-xs">
            rule: {decision.triggering_rule}
          </span>
        )}
        {decision.confidence !== null && (
          <span className="text-text-tertiary font-mono text-xs">
            conf {decision.confidence.toFixed(2)}
          </span>
        )}
      </div>

      {decision.reason_human !== '' && (
        <p className="text-text-secondary text-sm" data-testid="replay-decision-reason">
          {decision.reason_human}
        </p>
      )}

      {decision.reason_codes.length > 0 && (
        <ul className="flex flex-wrap gap-1" data-testid="replay-decision-reason-codes">
          {decision.reason_codes.map((code) => (
            <li
              key={code}
              className="border-border-default text-text-secondary rounded-sm border px-1.5 py-0.5 font-mono text-[10px]"
            >
              {code}
            </li>
          ))}
        </ul>
      )}

      {decision.suppressed_by.length > 0 && (
        <p className="text-text-tertiary text-xs" data-testid="replay-decision-suppressed">
          suppressed by: {decision.suppressed_by.join(', ')}
        </p>
      )}

      {decision.researcher_hint !== null && decision.researcher_hint !== '' && (
        <p
          className="text-text-secondary text-xs italic"
          data-testid="replay-decision-researcher-hint"
        >
          researcher hint: {decision.researcher_hint}
        </p>
      )}

      {(decision.llm_prompt !== null || decision.llm_output !== null) && (
        <MouthBlock prompt={decision.llm_prompt} output={decision.llm_output} />
      )}

      <EvaluationsBlock state={evaluationsState} triggeringRule={decision.triggering_rule} />
    </div>
  );
}

function ActionPill({
  action,
  executed,
}: {
  action: string;
  executed: boolean;
}): React.ReactElement {
  // `stay_silent` is the moderator's default; render it neutrally so a
  // researcher scanning the panel isn't visually pulled toward it the
  // same way an executed speak action would draw the eye.
  const isSilent = action === 'stay_silent';
  const tone = isSilent
    ? 'text-text-secondary border-border-default'
    : executed
      ? 'text-emerald-700 border-emerald-300 bg-emerald-50 dark:text-emerald-300 dark:border-emerald-800 dark:bg-emerald-900/30'
      : 'text-amber-700 border-amber-300 bg-amber-50 dark:text-amber-300 dark:border-amber-800 dark:bg-amber-900/30';
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-xs font-medium ${tone}`}
      data-testid="replay-decision-action"
    >
      {action}
      {!isSilent && (executed ? ' · spoken' : ' · suppressed')}
    </span>
  );
}

function SourcePill({ source }: { source: string }): React.ReactElement {
  const label =
    source === 'rules_engine'
      ? 'engine'
      : source === 'researcher_manual'
        ? 'researcher'
        : source === 'researcher_whisper'
          ? 'whisper'
          : source;
  return (
    <span
      className="border-border-default text-text-tertiary rounded-full border bg-transparent px-2 py-0.5 text-[10px] uppercase tracking-wide"
      data-testid="replay-decision-source"
    >
      {label}
    </span>
  );
}

function MouthBlock({
  prompt,
  output,
}: {
  prompt: Record<string, unknown> | null;
  output: string | null;
}): React.ReactElement {
  return (
    <div
      className="border-border-default flex flex-col gap-2 rounded-md border p-3"
      data-testid="replay-decision-mouth"
    >
      <h3 className="text-text-primary text-xs font-medium uppercase tracking-wide">Mouth</h3>
      {output !== null && (
        <p
          className="text-text-primary border-l-2 border-emerald-300 pl-2 text-sm italic dark:border-emerald-800"
          data-testid="replay-decision-llm-output"
        >
          “{output}”
        </p>
      )}
      {prompt !== null && (
        <details className="text-text-tertiary text-xs">
          <summary className="cursor-pointer select-none">View prompt JSON</summary>
          <pre
            className="bg-surface-secondary mt-2 max-h-48 overflow-auto rounded p-2 text-[11px] leading-tight"
            data-testid="replay-decision-llm-prompt"
          >
            {JSON.stringify(prompt, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

function EvaluationsBlock({
  state,
  triggeringRule,
}: {
  state: EvaluationsState;
  triggeringRule: string | null;
}): React.ReactElement {
  return (
    <div className="flex flex-col gap-2" data-testid="replay-decision-evaluations">
      <h3 className="text-text-primary text-xs font-medium uppercase tracking-wide">
        Rule evaluations
      </h3>
      {state.kind === 'idle' && (
        <p className="text-text-tertiary text-xs italic">No evaluations loaded.</p>
      )}
      {state.kind === 'loading' && (
        <p className="text-text-tertiary text-xs italic">Loading evaluations…</p>
      )}
      {state.kind === 'error' && (
        <p className="text-text-tertiary text-xs">
          {state.code === 'not_found' && 'No evaluations recorded for this decision.'}
          {state.code === 'unauthorized' && 'Sign in again to view evaluations.'}
          {state.code === 'network' && 'Evaluation fetch failed — re-select the marker to retry.'}
        </p>
      )}
      {state.kind === 'ready' && (
        <EvaluationsTable rows={state.rows} triggeringRule={triggeringRule} />
      )}
    </div>
  );
}

function EvaluationsTable({
  rows,
  triggeringRule,
}: {
  rows: EvaluationDTO[];
  triggeringRule: string | null;
}): React.ReactElement {
  if (rows.length === 0) {
    return <p className="text-text-tertiary text-xs italic">No rule evaluations recorded.</p>;
  }
  // Sort so the firing rule (if any) lands first, then suppressed rules,
  // then everything else — researchers care first about what fired.
  const sorted = [...rows].sort((a, b) => rankRow(a) - rankRow(b));
  return (
    <table
      className="border-border-default w-full table-fixed border-collapse border text-xs"
      data-testid="replay-decision-evaluations-table"
    >
      <thead className="bg-surface-secondary text-text-tertiary">
        <tr>
          <th className="border-border-default border px-2 py-1 text-left font-medium">Rule</th>
          <th className="border-border-default border px-2 py-1 text-left font-medium">Version</th>
          <th className="border-border-default border px-2 py-1 text-left font-medium">Status</th>
          <th className="border-border-default border px-2 py-1 text-right font-medium">Conf</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((row) => (
          <tr
            key={row.id}
            className={
              row.ruleName === triggeringRule
                ? 'bg-emerald-50/40 dark:bg-emerald-900/20'
                : undefined
            }
            data-testid={`replay-decision-evaluation-${row.ruleName}`}
          >
            <td className="border-border-default border px-2 py-1 font-mono">{row.ruleName}</td>
            <td className="border-border-default text-text-tertiary border px-2 py-1 font-mono">
              {row.ruleVersion}
            </td>
            <td className="border-border-default border px-2 py-1">
              <StatusCell fired={row.fired} suppressedReason={row.suppressedReason} />
            </td>
            <td className="border-border-default border px-2 py-1 text-right font-mono">
              {row.confidence.toFixed(2)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StatusCell({
  fired,
  suppressedReason,
}: {
  fired: boolean;
  suppressedReason: string | null;
}): React.ReactElement {
  if (!fired) {
    return <span className="text-text-tertiary">did not fire</span>;
  }
  if (suppressedReason !== null) {
    return (
      <span className="text-amber-700 dark:text-amber-400">
        fired · suppressed ({suppressedReason})
      </span>
    );
  }
  return <span className="text-emerald-700 dark:text-emerald-400">fired</span>;
}

function rankRow(row: EvaluationDTO): number {
  if (row.fired && row.suppressedReason === null) return 0; // chose this rule
  if (row.fired) return 1; // fired but lost
  return 2; // didn't fire
}
