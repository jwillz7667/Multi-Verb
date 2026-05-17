'use client';

/**
 * Replay timeline — the visual spine of the replay view (brief §11.2).
 *
 * Three vertically stacked swimlanes over a shared time axis:
 *
 *   - speech bands, one per participant, colored deterministically by
 *     participant id (so the same person is the same color across
 *     timelines for the same study),
 *   - decision markers, colored by source (rules_engine vs
 *     researcher_manual vs researcher_whisper) — the brief's audit-
 *     trail rule means even `stay_silent` decisions render here so a
 *     researcher can answer "why didn't it say anything",
 *   - flag markers (researcher bookmarks + auto-generated flags),
 *
 * Interactions:
 *
 *   - click anywhere on the time axis → seek (drives `currentTs` in
 *     the shell, which the audio player + state pane consume),
 *   - click a decision marker → select + seek (the detail pane in L8
 *     listens to `selectedDecisionId`),
 *   - click a flag marker → seek to the flag's ts,
 *   - hover a marker → tooltip with ts + content,
 *   - wheel → horizontal pan; ⌘/ctrl + wheel → zoom around the cursor.
 *
 * Coordinate model: the SVG renders into a fixed `[0..VIEW_WIDTH]`
 * coordinate space — the browser scales that to whatever pixel width
 * the container provides via `viewBox`. Time → x is a linear scale
 * over `[viewStartMs, viewEndMs]` (the currently-visible window),
 * which lets us implement pan/zoom by mutating just two numbers
 * without re-laying out anything.
 *
 * L5 renders the data in the bootstrap window (5 minutes by default).
 * Deeper ranges become available when L6/L7 add on-demand fetches.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type {
  ReplayDecisionDTO,
  ReplayFlagDTO,
  ReplayParticipantDTO,
  ReplaySessionDTO,
  ReplayUtteranceDTO,
} from './replay-types';

interface Props {
  session: ReplaySessionDTO;
  participants: ReplayParticipantDTO[];
  utterances: ReplayUtteranceDTO[];
  decisions: ReplayDecisionDTO[];
  flags: ReplayFlagDTO[];
  currentTs: string;
  selectedDecisionId: string | null;
  onSeek: (ts: string) => void;
  onSelectDecision: (decisionId: string) => void;
}

// SVG coordinate space. CSS scales this to whatever pixel width the
// container gives us — picking a round 1000 means click→time math
// stays readable.
const VIEW_WIDTH = 1000;
const PADDING_X = 24;
const PADDING_TOP = 22;
const AXIS_HEIGHT = 14;
const PARTICIPANT_BAND_HEIGHT = 14;
const PARTICIPANT_BAND_GAP = 4;
const DECISION_LANE_HEIGHT = 22;
const FLAG_LANE_HEIGHT = 18;
const LANE_GAP = 6;
const MIN_VIEW_SPAN_MS = 5_000; // hard floor on zoom-in so the axis stays legible
const MIN_UTTERANCE_WIDTH = 1.5; // sub-second utterances render as a thin tick

// Deterministic participant palette. Index = participant order in the
// roster (joinedAt asc), so the first person to join is always the
// first color. Cycles past 5; cohorts > 5 are rare.
const PARTICIPANT_PALETTE = ['#4F46E5', '#059669', '#DC2626', '#0EA5E9', '#D97706'] as const;

const SOURCE_PALETTE: Record<string, string> = {
  rules_engine: '#D97706',
  researcher_manual: '#2563EB',
  researcher_whisper: '#7C3AED',
};
const SOURCE_FALLBACK_COLOR = '#6B7280';
const FLAG_COLOR = '#F59E0B';
const FLAG_AUTO_COLOR = '#9CA3AF';
const CURSOR_COLOR = '#EF4444';
const SELECTED_OUTLINE_COLOR = '#0F172A';

interface TooltipState {
  x: number;
  y: number;
  label: string;
  sub: string;
}

export function ReplayTimeline({
  session,
  participants,
  utterances,
  decisions,
  flags,
  currentTs,
  selectedDecisionId,
  onSeek,
  onSelectDecision,
}: Props): React.ReactElement {
  // Anchor + horizon for the time scale. `actualStart` is the wall
  // clock when the engine joined the room; `actualEnd` is the wall
  // clock when the session terminated. If either is missing we fall
  // back to the observed data so the timeline isn't empty.
  const { anchorMs, horizonMs } = useMemo(
    () => computeTimeRange(session, utterances, decisions, flags),
    [session, utterances, decisions, flags],
  );
  const sessionSpanMs = Math.max(horizonMs - anchorMs, MIN_VIEW_SPAN_MS);

  // The visible window, in milliseconds from `anchorMs`. We zoom and
  // pan by mutating these two numbers; everything downstream derives
  // from them.
  const [viewStartMs, setViewStartMs] = useState<number>(0);
  const [viewEndMs, setViewEndMs] = useState<number>(sessionSpanMs);

  // When the session/bootstrap changes (e.g. on navigation between
  // sessions), reset the view to fit. Without this, a deep-linked
  // replay would inherit a stale viewport.
  useEffect(() => {
    setViewStartMs(0);
    setViewEndMs(sessionSpanMs);
  }, [sessionSpanMs]);

  const participantColors = useMemo(() => indexPalette(participants), [participants]);

  // Speech band layout. Decision + flag lanes always exist regardless
  // of participant count so the layout doesn't reflow when an empty
  // session loads.
  const totalHeight =
    PADDING_TOP +
    AXIS_HEIGHT +
    LANE_GAP +
    Math.max(participants.length, 1) * PARTICIPANT_BAND_HEIGHT +
    Math.max(participants.length - 1, 0) * PARTICIPANT_BAND_GAP +
    LANE_GAP +
    DECISION_LANE_HEIGHT +
    LANE_GAP +
    FLAG_LANE_HEIGHT +
    PADDING_TOP;

  const axisY = PADDING_TOP;
  const bandsTopY = PADDING_TOP + AXIS_HEIGHT + LANE_GAP;
  const decisionLaneY =
    bandsTopY +
    Math.max(participants.length, 1) * PARTICIPANT_BAND_HEIGHT +
    Math.max(participants.length - 1, 0) * PARTICIPANT_BAND_GAP +
    LANE_GAP;
  const flagLaneY = decisionLaneY + DECISION_LANE_HEIGHT + LANE_GAP;

  const tsToX = useCallback(
    (ts: string): number => {
      const ms = Date.parse(ts) - anchorMs;
      const span = viewEndMs - viewStartMs;
      if (span <= 0) return PADDING_X;
      const fraction = (ms - viewStartMs) / span;
      return PADDING_X + fraction * (VIEW_WIDTH - 2 * PADDING_X);
    },
    [anchorMs, viewStartMs, viewEndMs],
  );

  const xToTs = useCallback(
    (xViewBox: number): string => {
      const span = viewEndMs - viewStartMs;
      const fraction = (xViewBox - PADDING_X) / (VIEW_WIDTH - 2 * PADDING_X);
      const ms = anchorMs + viewStartMs + fraction * span;
      return new Date(ms).toISOString();
    },
    [anchorMs, viewStartMs, viewEndMs],
  );

  // Convert a mouse event's clientX into the SVG's coordinate space.
  // CSS scales the SVG to the container width, so we have to divide
  // by the on-screen scale factor — otherwise a click on the right
  // half of a 600px-wide rendering would point to a ts past the right
  // edge of the underlying 1000-unit viewBox.
  const svgRef = useRef<SVGSVGElement | null>(null);
  const clientXToViewBoxX = useCallback((clientX: number): number => {
    const svg = svgRef.current;
    if (svg === null) return PADDING_X;
    const rect = svg.getBoundingClientRect();
    if (rect.width === 0) return PADDING_X;
    const fraction = (clientX - rect.left) / rect.width;
    return Math.max(0, Math.min(VIEW_WIDTH, fraction * VIEW_WIDTH));
  }, []);

  const handleAxisClick = useCallback(
    (event: React.MouseEvent<SVGRectElement>): void => {
      const xViewBox = clientXToViewBoxX(event.clientX);
      onSeek(xToTs(xViewBox));
    },
    [clientXToViewBoxX, onSeek, xToTs],
  );

  const handleWheel = useCallback(
    (event: React.WheelEvent<SVGSVGElement>): void => {
      // ⌘/ctrl + wheel = zoom around cursor. Plain wheel = horizontal
      // pan. We swallow the event either way so the page doesn't
      // scroll behind us.
      event.preventDefault();
      const span = viewEndMs - viewStartMs;
      if (event.ctrlKey || event.metaKey) {
        const scale = event.deltaY > 0 ? 1.15 : 1 / 1.15;
        const newSpan = Math.max(MIN_VIEW_SPAN_MS, Math.min(sessionSpanMs, span * scale));
        const xViewBox = clientXToViewBoxX(event.clientX);
        const fraction = (xViewBox - PADDING_X) / (VIEW_WIDTH - 2 * PADDING_X);
        const cursorMs = viewStartMs + fraction * span;
        let nextStart = cursorMs - fraction * newSpan;
        let nextEnd = nextStart + newSpan;
        if (nextStart < 0) {
          nextEnd -= nextStart;
          nextStart = 0;
        }
        if (nextEnd > sessionSpanMs) {
          const overshoot = nextEnd - sessionSpanMs;
          nextStart = Math.max(0, nextStart - overshoot);
          nextEnd = sessionSpanMs;
        }
        setViewStartMs(nextStart);
        setViewEndMs(nextEnd);
      } else {
        // Use deltaX if the browser routed a horizontal wheel; trackpads
        // tend to send pan via deltaX while mouse wheels send via deltaY.
        const delta = event.deltaX !== 0 ? event.deltaX : event.deltaY;
        const panMs = (delta / VIEW_WIDTH) * span;
        let nextStart = viewStartMs + panMs;
        let nextEnd = viewEndMs + panMs;
        if (nextStart < 0) {
          nextEnd -= nextStart;
          nextStart = 0;
        }
        if (nextEnd > sessionSpanMs) {
          const overshoot = nextEnd - sessionSpanMs;
          nextStart = Math.max(0, nextStart - overshoot);
          nextEnd = sessionSpanMs;
        }
        setViewStartMs(nextStart);
        setViewEndMs(nextEnd);
      }
    },
    [clientXToViewBoxX, sessionSpanMs, viewEndMs, viewStartMs],
  );

  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

  const utteranceBands = useMemo(() => {
    if (participants.length === 0) return [] as React.ReactElement[];
    const byParticipant = new Map<string, ReplayUtteranceDTO[]>();
    for (const u of utterances) {
      const existing = byParticipant.get(u.participant_id);
      if (existing === undefined) {
        byParticipant.set(u.participant_id, [u]);
      } else {
        existing.push(u);
      }
    }
    return participants.flatMap((p, idx) => {
      const bandY = bandsTopY + idx * (PARTICIPANT_BAND_HEIGHT + PARTICIPANT_BAND_GAP);
      const color = participantColors.get(p.id) ?? PARTICIPANT_PALETTE[0];
      const items = byParticipant.get(p.id) ?? [];
      return items.map((u) => {
        const x1 = tsToX(u.start_ts);
        const x2 = tsToX(u.end_ts);
        const width = Math.max(MIN_UTTERANCE_WIDTH, x2 - x1);
        return (
          <rect
            key={u.id}
            x={x1}
            y={bandY}
            width={width}
            height={PARTICIPANT_BAND_HEIGHT}
            fill={color}
            opacity={u.is_final ? 0.85 : 0.45}
            data-testid={`replay-timeline-utterance-${u.id}`}
            onMouseEnter={(e): void => {
              setTooltip({
                x: e.clientX,
                y: e.clientY,
                label: `${p.display_name} · ${formatClock(u.start_ts, anchorMs)}`,
                sub: u.text,
              });
            }}
            onMouseLeave={(): void => {
              setTooltip(null);
            }}
          />
        );
      });
    });
  }, [anchorMs, bandsTopY, participantColors, participants, tsToX, utterances]);

  const decisionMarkers = useMemo(
    () =>
      decisions.map((d) => {
        const x = tsToX(d.ts);
        const color = SOURCE_PALETTE[d.source] ?? SOURCE_FALLBACK_COLOR;
        const isSelected = d.id === selectedDecisionId;
        return (
          <g
            key={d.id}
            data-testid={`replay-timeline-decision-${d.id}`}
            data-source={d.source}
            onClick={(e): void => {
              e.stopPropagation();
              onSelectDecision(d.id);
              onSeek(d.ts);
            }}
            onMouseEnter={(e): void => {
              setTooltip({
                x: e.clientX,
                y: e.clientY,
                label: `${d.action} · ${d.source}`,
                sub:
                  d.reason_human !== ''
                    ? d.reason_human
                    : (d.triggering_rule ?? d.reason_codes.join(', ')),
              });
            }}
            onMouseLeave={(): void => {
              setTooltip(null);
            }}
            style={{ cursor: 'pointer' }}
          >
            <circle
              cx={x}
              cy={decisionLaneY + DECISION_LANE_HEIGHT / 2}
              r={d.was_executed ? 5 : 4}
              fill={d.was_executed ? color : 'transparent'}
              stroke={color}
              strokeWidth={isSelected ? 2.5 : 1.5}
            />
            {isSelected ? (
              <circle
                cx={x}
                cy={decisionLaneY + DECISION_LANE_HEIGHT / 2}
                r={8}
                fill="none"
                stroke={SELECTED_OUTLINE_COLOR}
                strokeWidth={1}
                strokeDasharray="2 2"
              />
            ) : null}
          </g>
        );
      }),
    [decisions, decisionLaneY, onSeek, onSelectDecision, selectedDecisionId, tsToX],
  );

  const flagMarkers = useMemo(
    () =>
      flags.map((f) => {
        const x = tsToX(f.ts);
        const color = f.auto_generated ? FLAG_AUTO_COLOR : FLAG_COLOR;
        return (
          <g
            key={f.id}
            data-testid={`replay-timeline-flag-${f.id}`}
            onClick={(e): void => {
              e.stopPropagation();
              onSeek(f.ts);
            }}
            onMouseEnter={(e): void => {
              setTooltip({
                x: e.clientX,
                y: e.clientY,
                label: `Flag · ${formatClock(f.ts, anchorMs)}`,
                sub: f.note ?? (f.auto_generated ? 'auto-generated' : 'researcher bookmark'),
              });
            }}
            onMouseLeave={(): void => {
              setTooltip(null);
            }}
            style={{ cursor: 'pointer' }}
          >
            <polygon
              points={`${(x - 5).toString()},${flagLaneY.toString()} ${(x + 5).toString()},${flagLaneY.toString()} ${x.toString()},${(flagLaneY + FLAG_LANE_HEIGHT).toString()}`}
              fill={color}
            />
          </g>
        );
      }),
    [anchorMs, flags, flagLaneY, onSeek, tsToX],
  );

  // Axis ticks: aim for ~6 labels across the visible window. The
  // exact interval steps through a fixed sequence so labels land on
  // human-friendly multiples (5s, 10s, 30s, 1m, 5m, …).
  const tickIntervalMs = chooseTickInterval(viewEndMs - viewStartMs);
  const tickStartMs = Math.ceil(viewStartMs / tickIntervalMs) * tickIntervalMs;
  const ticks: { x: number; label: string }[] = [];
  for (let ms = tickStartMs; ms <= viewEndMs; ms += tickIntervalMs) {
    const x = tsToX(new Date(anchorMs + ms).toISOString());
    ticks.push({ x, label: formatOffset(ms) });
  }

  const cursorX = tsToX(currentTs);

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${VIEW_WIDTH.toString()} ${totalHeight.toString()}`}
        preserveAspectRatio="none"
        width="100%"
        height={totalHeight}
        onWheel={handleWheel}
        data-testid="replay-timeline-svg"
        role="img"
        aria-label="Session timeline"
      >
        {/* Background click target for seeking — the axis row plus the
            full lane stack so a click anywhere in empty space seeks.
            Markers' onClick stop propagation so they don't double-fire. */}
        <rect
          x={PADDING_X}
          y={axisY - 4}
          width={VIEW_WIDTH - 2 * PADDING_X}
          height={totalHeight - PADDING_TOP - 4}
          fill="transparent"
          onClick={handleAxisClick}
          data-testid="replay-timeline-seek-surface"
          style={{ cursor: 'pointer' }}
        />

        {/* Axis baseline + ticks */}
        <line
          x1={PADDING_X}
          x2={VIEW_WIDTH - PADDING_X}
          y1={axisY + AXIS_HEIGHT - 1}
          y2={axisY + AXIS_HEIGHT - 1}
          stroke="#CBD5E1"
          strokeWidth={1}
        />
        {ticks.map((t) => (
          <g key={`${t.x.toString()}-${t.label}`}>
            <line
              x1={t.x}
              x2={t.x}
              y1={axisY + AXIS_HEIGHT - 4}
              y2={axisY + AXIS_HEIGHT - 1}
              stroke="#94A3B8"
              strokeWidth={1}
            />
            <text
              x={t.x}
              y={axisY + AXIS_HEIGHT - 6}
              fontSize="9"
              textAnchor="middle"
              fill="#64748B"
              fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
            >
              {t.label}
            </text>
          </g>
        ))}

        {/* Speech bands — one row per participant. An empty band still
            renders an outline so the lane reads as "present but quiet"
            rather than "missing". */}
        {participants.map((p, idx) => {
          const bandY = bandsTopY + idx * (PARTICIPANT_BAND_HEIGHT + PARTICIPANT_BAND_GAP);
          const color = participantColors.get(p.id) ?? PARTICIPANT_PALETTE[0];
          return (
            <g key={p.id} data-testid={`replay-timeline-band-${p.id}`} data-color={color}>
              <rect
                x={PADDING_X}
                y={bandY}
                width={VIEW_WIDTH - 2 * PADDING_X}
                height={PARTICIPANT_BAND_HEIGHT}
                fill="#F1F5F9"
                stroke="#E2E8F0"
                strokeWidth={0.5}
              />
              <text
                x={PADDING_X - 6}
                y={bandY + PARTICIPANT_BAND_HEIGHT - 3}
                fontSize="9"
                textAnchor="end"
                fill="#475569"
                fontFamily="ui-sans-serif, system-ui"
              >
                {p.display_name}
              </text>
            </g>
          );
        })}
        {utteranceBands}

        {/* Decision lane background + markers */}
        <line
          x1={PADDING_X}
          x2={VIEW_WIDTH - PADDING_X}
          y1={decisionLaneY + DECISION_LANE_HEIGHT / 2}
          y2={decisionLaneY + DECISION_LANE_HEIGHT / 2}
          stroke="#E2E8F0"
          strokeWidth={1}
        />
        <text
          x={PADDING_X - 6}
          y={decisionLaneY + DECISION_LANE_HEIGHT / 2 + 3}
          fontSize="9"
          textAnchor="end"
          fill="#475569"
          fontFamily="ui-sans-serif, system-ui"
        >
          decisions
        </text>
        {decisionMarkers}

        {/* Flag lane */}
        <text
          x={PADDING_X - 6}
          y={flagLaneY + FLAG_LANE_HEIGHT - 4}
          fontSize="9"
          textAnchor="end"
          fill="#475569"
          fontFamily="ui-sans-serif, system-ui"
        >
          flags
        </text>
        {flagMarkers}

        {/* Scrubber cursor — drawn last so it's always on top of
            everything else. */}
        <line
          x1={cursorX}
          x2={cursorX}
          y1={axisY}
          y2={totalHeight - PADDING_TOP / 2}
          stroke={CURSOR_COLOR}
          strokeWidth={1.5}
          data-testid="replay-timeline-cursor"
        />
      </svg>

      {tooltip !== null ? (
        <div
          className="border-border-default bg-surface-primary text-text-primary pointer-events-none fixed z-50 max-w-xs rounded-md border px-3 py-2 text-xs shadow-lg"
          style={{ left: tooltip.x + 12, top: tooltip.y + 12 }}
          data-testid="replay-timeline-tooltip"
        >
          <div className="font-medium">{tooltip.label}</div>
          {tooltip.sub !== '' ? (
            <div className="text-text-secondary mt-1 line-clamp-3">{tooltip.sub}</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function computeTimeRange(
  session: ReplaySessionDTO,
  utterances: readonly ReplayUtteranceDTO[],
  decisions: readonly ReplayDecisionDTO[],
  flags: readonly ReplayFlagDTO[],
): { anchorMs: number; horizonMs: number } {
  const startCandidates: number[] = [];
  if (session.actual_start !== null) startCandidates.push(Date.parse(session.actual_start));
  if (session.scheduled_start !== null) startCandidates.push(Date.parse(session.scheduled_start));
  const firstUtterance = utterances[0];
  if (firstUtterance !== undefined) startCandidates.push(Date.parse(firstUtterance.start_ts));
  const firstDecision = decisions[0];
  if (firstDecision !== undefined) startCandidates.push(Date.parse(firstDecision.ts));
  const firstFlag = flags[0];
  if (firstFlag !== undefined) startCandidates.push(Date.parse(firstFlag.ts));
  const anchorMs =
    startCandidates.length === 0 ? Date.parse(session.created_at) : Math.min(...startCandidates);

  const endCandidates: number[] = [];
  if (session.actual_end !== null) endCandidates.push(Date.parse(session.actual_end));
  const lastUtterance = utterances[utterances.length - 1];
  if (lastUtterance !== undefined) endCandidates.push(Date.parse(lastUtterance.end_ts));
  const lastDecision = decisions[decisions.length - 1];
  if (lastDecision !== undefined) endCandidates.push(Date.parse(lastDecision.ts));
  const lastFlag = flags[flags.length - 1];
  if (lastFlag !== undefined) endCandidates.push(Date.parse(lastFlag.ts));
  const horizonMs =
    endCandidates.length === 0 ? anchorMs + MIN_VIEW_SPAN_MS : Math.max(...endCandidates);

  return { anchorMs, horizonMs };
}

function indexPalette(participants: readonly ReplayParticipantDTO[]): Map<string, string> {
  const m = new Map<string, string>();
  participants.forEach((p, idx) => {
    const color = PARTICIPANT_PALETTE[idx % PARTICIPANT_PALETTE.length] ?? PARTICIPANT_PALETTE[0];
    m.set(p.id, color);
  });
  return m;
}

const TICK_INTERVALS_MS = [
  1_000, 2_000, 5_000, 10_000, 15_000, 30_000, 60_000, 120_000, 300_000, 600_000, 1_800_000,
  3_600_000,
] as const;

const MAX_TICK_INTERVAL_MS = 3_600_000;

function chooseTickInterval(spanMs: number): number {
  // Aim for ~6 visible ticks. Pick the smallest interval that doesn't
  // overflow that budget so the axis stays uncluttered.
  const target = spanMs / 6;
  for (const i of TICK_INTERVALS_MS) {
    if (i >= target) return i;
  }
  return MAX_TICK_INTERVAL_MS;
}

function formatOffset(ms: number): string {
  const totalSec = Math.round(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) {
    return `${h.toString()}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }
  return `${m.toString()}:${s.toString().padStart(2, '0')}`;
}

function formatClock(ts: string, anchorMs: number): string {
  const offsetMs = Date.parse(ts) - anchorMs;
  return formatOffset(Math.max(0, offsetMs));
}
