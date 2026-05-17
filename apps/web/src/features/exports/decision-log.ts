/**
 * Decision log export — pure CSV formatter.
 *
 * Audit principle (brief §2.2): every decision is auditable, including
 * the `stay_silent` ones. The CSV therefore exposes the full schema
 * for each row: action, source, triggering rule, reason codes, the
 * mouth output if any, execution + cooldown timestamps, and the
 * suppressed-by list. A researcher can grep this file for "why did the
 * moderator stay quiet at 12:34?" and see the rule that almost fired
 * plus what won out.
 *
 * RFC 4180 quoting: any field that contains a comma, quote, CR, or LF
 * gets wrapped in double quotes with internal quotes doubled. Arrays
 * are joined with `; ` (semicolon + space) so they read naturally in
 * spreadsheet apps that don't grok nested commas — but `; ` is rare
 * enough in rule names / reason codes that round-tripping is fine.
 *
 * Why a separate module from `transcript.ts`: the two formats share
 * nothing structural (CSV row-oriented, .txt/.vtt cue-oriented) and
 * keeping them split lets each file stay shallow + testable.
 */

import type { DecisionRow, ParticipantRow } from '@/features/sessions';

export const DECISION_LOG_COLUMNS = [
  'ts',
  'tick_id',
  'action',
  'target_participant_id',
  'target_participant_name',
  'source',
  'triggering_rule',
  'researcher_id',
  'researcher_hint',
  'reason_codes',
  'reason_human',
  'confidence',
  'was_executed',
  'suppressed_by',
  'llm_output',
  'spoken_at',
  'cooldown_until',
] as const;

export type DecisionLogColumn = (typeof DECISION_LOG_COLUMNS)[number];

/**
 * Render the CSV header row. Exposed for tests + callers that want to
 * stream the header before the body.
 */
export function formatDecisionLogHeader(): string {
  return DECISION_LOG_COLUMNS.join(',');
}

/**
 * Render a single decision as one CSV row, joined target-name into the
 * row from the participants lookup. `participantsById` maps
 * `participant.id` → row, so the formatter doesn't need to query the DB
 * per decision.
 */
export function formatDecisionLogRow(
  decision: DecisionRow,
  participantsById: ReadonlyMap<string, ParticipantRow>,
): string {
  const targetName =
    decision.targetParticipantId !== null
      ? (participantsById.get(decision.targetParticipantId)?.displayName ?? '')
      : '';
  const cells: string[] = [
    decision.ts.toISOString(),
    decision.tickId.toString(),
    decision.action,
    decision.targetParticipantId ?? '',
    targetName,
    decision.source,
    decision.triggeringRule ?? '',
    decision.researcherId ?? '',
    decision.researcherHint ?? '',
    decision.reasonCodes.join('; '),
    decision.reasonHuman,
    decision.confidence === null ? '' : decision.confidence.toString(),
    decision.wasExecuted ? 'true' : 'false',
    decision.suppressedBy.join('; '),
    decision.llmOutput ?? '',
    decision.spokenAt !== null ? decision.spokenAt.toISOString() : '',
    decision.cooldownUntil.toISOString(),
  ];
  return cells.map(escapeCsvCell).join(',');
}

/**
 * Render the full CSV including header. The all-in-one form is fine
 * for the in-memory path (decision counts are bounded — even a 90-min
 * dense session produces ≈ 11k rows ≈ 2 MB pre-compression). If
 * sessions grow past that envelope, switch to a `ReadableStream` that
 * writes the header once then yields `formatDecisionLogRow` per chunk.
 */
export function formatDecisionLogCsv(
  decisions: readonly DecisionRow[],
  participantsById: ReadonlyMap<string, ParticipantRow>,
): string {
  const lines: string[] = [formatDecisionLogHeader()];
  for (const d of decisions) {
    lines.push(formatDecisionLogRow(d, participantsById));
  }
  // CSV convention: trailing newline so a downstream concat doesn't
  // glue the last row to whatever comes after.
  return lines.join('\n') + '\n';
}

function escapeCsvCell(raw: string): string {
  // RFC 4180: quote if the cell contains comma, quote, CR, or LF. A
  // leading/trailing space is also worth quoting because some
  // spreadsheet apps trim it on import; cheap to quote conservatively.
  if (/[",\r\n]/.test(raw) || raw !== raw.trim()) {
    return `"${raw.replace(/"/g, '""')}"`;
  }
  return raw;
}
