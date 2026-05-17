'use client';

/**
 * DecisionLog + "Why quiet now?" — shadow-mode audit dashboard (Phase 3 L12).
 *
 * The moderator is biased toward silence (brief §2.1). To validate that
 * bias before Phase 4 lets it speak, researchers need to see, in real
 * time:
 *
 *   - Every decision the engine made (one per 2 Hz tick — mostly
 *     `stay_silent` in shadow mode).
 *   - For the most recent decision, *why* the engine landed where it
 *     did: which rule fired (if any), which were suppressed, and what
 *     suppressed them (quietness budget, cooldown, lost priority).
 *
 * The component subscribes to the same `EventSource` that powers the
 * participant tiles + live transcript — sharing one socket per page
 * keeps SSE-server connection counts predictable and avoids three
 * separate reconnect dances on a flaky link.
 *
 * Click a decision to expand it: that triggers a one-shot fetch of
 * `rule_evaluations` for the row from
 * `/api/sessions/[id]/decisions/[decisionId]/evaluations`. The
 * evaluations don't ride the SSE wire — a v1 session would push 6 rule
 * rows × 7200 ticks = ~43k rows per hour into every connected client,
 * which is wasteful when the typical researcher only inspects a handful
 * of moments.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { parseTranscriptEvent, type ModeratorDecisionValidated } from '@/features/sessions/events';

interface Props {
  sessionId: string;
}

interface RuleEvaluation {
  id: string;
  ruleName: string;
  ruleVersion: string;
  fired: boolean;
  suppressedReason: string | null;
  confidence: number;
}

interface EvaluationsResponse {
  decision_id: string;
  evaluations: RuleEvaluation[];
}

type ConnectionState = 'connecting' | 'open' | 'closed';

// Cap the in-memory log so a long shadow-mode run doesn't bloat the
// page. 240 = 2 minutes at 2 Hz, matching the SSE backfill cap.
const MAX_DECISIONS = 240;

export function DecisionLog({ sessionId }: Props): React.ReactElement {
  const [decisions, setDecisions] = useState<ModeratorDecisionValidated[]>([]);
  const [state, setState] = useState<ConnectionState>('connecting');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [evaluations, setEvaluations] = useState<RuleEvaluation[] | null>(null);
  const [evaluationsErr, setEvaluationsErr] = useState<string | null>(null);
  const [loadingEvaluations, setLoadingEvaluations] = useState(false);

  useEffect(() => {
    const source = new EventSource(`/api/sessions/${sessionId}/events`);

    const onReady = (): void => {
      setState('open');
    };

    const onDecision = (raw: MessageEvent<string>): void => {
      const parsed = parseTranscriptEvent(safeJsonParse(raw.data));
      if (parsed?.type !== 'decision') return;
      // Discriminated-union narrowing on `parsed.type` makes the payload.decision
      // access type-safe without an explicit cast.
      setDecisions((prev) => mergeDecision(prev, parsed.payload.decision));
    };

    const onOpen = (): void => {
      setState('open');
    };

    const onError = (): void => {
      setState(source.readyState === EventSource.CLOSED ? 'closed' : 'connecting');
    };

    source.addEventListener('ready', onReady);
    source.addEventListener('decision', onDecision);
    source.addEventListener('open', onOpen);
    source.addEventListener('error', onError);

    return () => {
      source.removeEventListener('ready', onReady);
      source.removeEventListener('decision', onDecision);
      source.removeEventListener('open', onOpen);
      source.removeEventListener('error', onError);
      source.close();
    };
  }, [sessionId]);

  // The "Why quiet now?" panel defaults to the latest decision unless
  // the researcher has pinned an older row by clicking it. Reset the
  // pin when the underlying list changes so a freshly-selected row
  // remains visible until they explicitly re-pin.
  const latestDecision = useMemo<ModeratorDecisionValidated | null>(() => {
    if (decisions.length === 0) return null;
    return decisions[decisions.length - 1] ?? null;
  }, [decisions]);

  const selectedDecision = useMemo<ModeratorDecisionValidated | null>(() => {
    if (selectedId === null) return latestDecision;
    return decisions.find((d) => d.decision_id === selectedId) ?? latestDecision;
  }, [selectedId, latestDecision, decisions]);

  // Fetch rule_evaluations for whichever decision is in focus. Aborts
  // on unmount or when the focused decision changes mid-flight so we
  // never write a stale response into state.
  useEffect(() => {
    if (selectedDecision === null) {
      setEvaluations(null);
      setEvaluationsErr(null);
      return;
    }
    const decisionId = selectedDecision.decision_id;
    const controller = new AbortController();
    setLoadingEvaluations(true);
    setEvaluationsErr(null);
    fetch(`/api/sessions/${sessionId}/decisions/${decisionId}/evaluations`, {
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as EvaluationsResponse;
      })
      .then((data) => {
        // Drop a late response that belongs to a previously-selected decision.
        if (data.decision_id !== decisionId) return;
        setEvaluations(data.evaluations);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setEvaluationsErr(err instanceof Error ? err.message : 'load_failed');
      })
      .finally(() => {
        setLoadingEvaluations(false);
      });
    return () => {
      controller.abort();
    };
  }, [selectedDecision, sessionId]);

  const handleSelect = useCallback((decisionId: string) => {
    setSelectedId((current) => (current === decisionId ? null : decisionId));
  }, []);

  const statusLabel =
    state === 'open' ? 'Live' : state === 'connecting' ? 'Connecting…' : 'Disconnected';
  const statusDot =
    state === 'open'
      ? 'bg-green-500'
      : state === 'connecting'
        ? 'bg-yellow-500 animate-pulse'
        : 'bg-red-500';

  return (
    <section className="border-border-default bg-surface-primary flex flex-col gap-4 rounded-lg border p-6">
      <div className="flex items-center justify-between">
        <h2 className="text-text-primary text-lg font-medium">Decisions (shadow mode)</h2>
        <span className="text-text-secondary flex items-center gap-2 text-xs">
          <span className={`inline-block h-2 w-2 rounded-full ${statusDot}`} />
          {statusLabel}
        </span>
      </div>

      <WhyQuietPanel
        decision={selectedDecision}
        evaluations={evaluations}
        loading={loadingEvaluations}
        error={evaluationsErr}
      />

      <DecisionList
        decisions={decisions}
        selectedId={selectedDecision?.decision_id ?? null}
        onSelect={handleSelect}
      />
    </section>
  );
}

interface WhyQuietPanelProps {
  decision: ModeratorDecisionValidated | null;
  evaluations: RuleEvaluation[] | null;
  loading: boolean;
  error: string | null;
}

function WhyQuietPanel({
  decision,
  evaluations,
  loading,
  error,
}: WhyQuietPanelProps): React.ReactElement {
  if (decision === null) {
    return (
      <div className="border-border-default bg-surface-secondary rounded-md border border-dashed p-4">
        <p className="text-text-tertiary text-sm italic">
          Waiting for the first decision. The tick loop publishes one per 500 ms.
        </p>
      </div>
    );
  }

  const isSilent = decision.action === 'stay_silent';
  const heading = isSilent ? 'Why is the moderator quiet?' : 'Why did the moderator speak?';

  return (
    <div className="border-border-default bg-surface-secondary flex flex-col gap-3 rounded-md border p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-text-primary text-sm font-semibold">{heading}</h3>
        <span className="text-text-tertiary font-mono text-xs">
          tick {decision.tick_id} · {formatTime(decision.timestamp)}
        </span>
      </div>

      <dl className="text-text-secondary grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
        <dt>Action</dt>
        <dd className="font-mono">{decision.action}</dd>
        {decision.triggering_rule !== null && (
          <>
            <dt>Triggered by</dt>
            <dd className="font-mono">{decision.triggering_rule}</dd>
          </>
        )}
        {decision.reason_human !== '' && (
          <>
            <dt>Rationale</dt>
            <dd>{decision.reason_human}</dd>
          </>
        )}
        {decision.suppressed_by.length > 0 && (
          <>
            <dt>Suppressed by</dt>
            <dd className="font-mono">{decision.suppressed_by.join(', ')}</dd>
          </>
        )}
        {decision.reason_codes.length > 0 && (
          <>
            <dt>Reason codes</dt>
            <dd className="font-mono">{decision.reason_codes.join(', ')}</dd>
          </>
        )}
      </dl>

      <RuleEvaluationsTable evaluations={evaluations} loading={loading} error={error} />
    </div>
  );
}

interface RuleEvaluationsTableProps {
  evaluations: RuleEvaluation[] | null;
  loading: boolean;
  error: string | null;
}

function RuleEvaluationsTable({
  evaluations,
  loading,
  error,
}: RuleEvaluationsTableProps): React.ReactElement {
  if (loading && evaluations === null) {
    return <p className="text-text-tertiary text-xs italic">Loading per-rule audit…</p>;
  }
  if (error !== null) {
    return <p className="text-xs text-red-500">Failed to load rule evaluations: {error}</p>;
  }
  if (evaluations === null || evaluations.length === 0) {
    return (
      <p className="text-text-tertiary text-xs italic">
        No per-rule evaluations recorded for this tick.
      </p>
    );
  }
  return (
    <div className="border-border-default overflow-hidden rounded border">
      <table className="text-text-secondary w-full text-left text-xs">
        <thead className="text-text-tertiary bg-surface-tertiary">
          <tr>
            <th className="px-2 py-1 font-medium">Rule</th>
            <th className="px-2 py-1 font-medium">Version</th>
            <th className="px-2 py-1 font-medium">Verdict</th>
            <th className="px-2 py-1 font-medium">Confidence</th>
          </tr>
        </thead>
        <tbody>
          {evaluations.map((ev) => (
            <tr key={ev.id} className="border-border-default border-t">
              <td className="px-2 py-1 font-mono">{ev.ruleName}</td>
              <td className="text-text-tertiary px-2 py-1 font-mono">{ev.ruleVersion}</td>
              <td className="px-2 py-1 font-mono">
                {ev.fired ? (
                  <span className="text-green-600">fired</span>
                ) : ev.suppressedReason !== null ? (
                  <span className="text-text-tertiary">suppressed · {ev.suppressedReason}</span>
                ) : (
                  <span className="text-text-tertiary">did not fire</span>
                )}
              </td>
              <td className="px-2 py-1 font-mono">{ev.confidence.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface DecisionListProps {
  decisions: ModeratorDecisionValidated[];
  selectedId: string | null;
  onSelect: (decisionId: string) => void;
}

function DecisionList({ decisions, selectedId, onSelect }: DecisionListProps): React.ReactElement {
  if (decisions.length === 0) {
    return (
      <p className="text-text-tertiary text-sm italic">
        Decision log will appear here once the engine starts ticking.
      </p>
    );
  }
  // Newest-first feels right when researchers want to glance at "what
  // just happened" — but the SSE stream appends in tick order. Render
  // reversed without mutating the original array.
  const ordered = decisions.slice().reverse();
  return (
    <div className="border-border-default bg-surface-secondary max-h-72 overflow-y-auto rounded-md border">
      <ul className="divide-border-default divide-y">
        {ordered.map((d) => {
          const isSelected = d.decision_id === selectedId;
          return (
            <li key={d.decision_id}>
              <button
                type="button"
                onClick={() => {
                  onSelect(d.decision_id);
                }}
                className={`flex w-full items-center gap-3 px-3 py-2 text-left text-xs transition-colors ${
                  isSelected
                    ? 'bg-surface-tertiary text-text-primary'
                    : 'text-text-secondary hover:bg-surface-tertiary/40'
                }`}
              >
                <span className="text-text-tertiary w-16 shrink-0 font-mono">
                  {formatTime(d.timestamp)}
                </span>
                <span
                  className={`w-32 shrink-0 font-mono ${
                    d.action === 'stay_silent' ? 'text-text-tertiary' : 'text-text-primary'
                  }`}
                >
                  {d.action}
                </span>
                <span className="text-text-tertiary shrink-0 font-mono">tick {d.tick_id}</span>
                <span className="flex-1 truncate font-mono text-[10px]">
                  {d.triggering_rule ?? d.suppressed_by[0] ?? '—'}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function mergeDecision(
  prev: ModeratorDecisionValidated[],
  next: ModeratorDecisionValidated,
): ModeratorDecisionValidated[] {
  const existing = prev.findIndex((d) => d.decision_id === next.decision_id);
  if (existing >= 0) {
    const copy = prev.slice();
    copy[existing] = next;
    return copy;
  }
  const appended = [...prev, next];
  if (appended.length > MAX_DECISIONS) {
    return appended.slice(appended.length - MAX_DECISIONS);
  }
  return appended;
}

function safeJsonParse(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
