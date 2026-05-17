/**
 * Transcript export — pure formatters for .txt and .vtt output.
 *
 * The transcript is the merged stream of participant utterances and
 * moderator turns. Participant lines come from `utterances` (final
 * rows only); moderator lines come from executed decisions whose
 * mouth produced an `llm_output`. They are interleaved and sorted by
 * start time so a reader sees the conversation in the order the room
 * heard it.
 *
 * Moderator turn duration is estimated from word count at ~150 wpm,
 * clamped to a sane [1.0s, 10.0s] envelope. The TTS layer doesn't
 * persist a finalized end-time per cue (it streams audio and moves
 * on), so this is the best signal we have without going back to the
 * R2 recording.
 *
 * Why pure formatters in `features/exports/` instead of inline in the
 * route handler:
 *   - they're trivially unit-testable with no DB,
 *   - the CSV / JSONL exports (L11, L12) will share the same module
 *     boundary so the route layer stays a thin assembler,
 *   - the formatters are deterministic — the same `(utterances,
 *     decisions, anchor)` always produces the same bytes, which makes
 *     diffing two export attempts straightforward for researchers.
 */
import type { DecisionRow, UtteranceWithSpeakerRow } from '@/features/sessions';

export interface TranscriptLine {
  /** Milliseconds since `anchor` — 0 if the row is at or before anchor. */
  startMs: number;
  endMs: number;
  speaker: string;
  speakerKind: 'participant' | 'moderator';
  text: string;
}

export interface TranscriptHeader {
  sessionId: string;
  livekitRoomName: string;
  actualStart: Date | null;
  actualEnd: Date | null;
  participantNames: string[];
}

const MODERATOR_SPEAKER_LABEL = 'Moderator';
const MODERATOR_WPM = 150;
const MODERATOR_MIN_DURATION_MS = 1_000;
const MODERATOR_MAX_DURATION_MS = 10_000;
const MODERATOR_DEFAULT_DURATION_MS = 5_000;

/**
 * Merge utterance rows + executed moderator decisions into a single
 * sorted, anchored transcript stream.
 *
 * `anchor` is the wall-clock zero for the export — typically
 * `session.actualStart`. Lines before the anchor are clamped to 0; we
 * never emit a negative offset since downstream timecode formats
 * (WebVTT, our human-readable clock) don't represent them.
 */
export function buildTranscriptLines(args: {
  anchor: Date;
  utterances: UtteranceWithSpeakerRow[];
  moderatorDecisions: DecisionRow[];
}): TranscriptLine[] {
  const anchorMs = args.anchor.getTime();
  const lines: TranscriptLine[] = [];

  for (const u of args.utterances) {
    if (!u.isFinal) continue;
    const text = u.text.trim();
    if (text === '') continue;
    const startMs = Math.max(0, u.startTs.getTime() - anchorMs);
    const endMsRaw = Math.max(0, u.endTs.getTime() - anchorMs);
    // Guarantee a minimum 500ms cue length so WebVTT players have time
    // to render the line — collapsed cues vanish before the eye lands.
    // Affects two cases: pre-anchor utterances (both clamped to 0) and
    // sub-500ms STT segments (rare but real for back-channels like
    // "yeah").
    const endMs = Math.max(endMsRaw, startMs + 500);
    lines.push({
      startMs,
      endMs,
      speaker: u.participantDisplayName,
      speakerKind: 'participant',
      text,
    });
  }

  for (const d of args.moderatorDecisions) {
    if (!d.wasExecuted) continue;
    if (d.llmOutput === null) continue;
    const text = d.llmOutput.trim();
    if (text === '') continue;
    const spokenAt = d.spokenAt ?? d.ts;
    const startMs = Math.max(0, spokenAt.getTime() - anchorMs);
    lines.push({
      startMs,
      endMs: startMs + estimateModeratorDurationMs(text),
      speaker: MODERATOR_SPEAKER_LABEL,
      speakerKind: 'moderator',
      text,
    });
  }

  // `(startMs, speaker)` keeps the order deterministic when a moderator
  // utterance lands at the same ms as a participant utterance —
  // unlikely in practice but cheap to pin down for snapshot tests.
  lines.sort((a, b) => {
    if (a.startMs !== b.startMs) return a.startMs - b.startMs;
    if (a.speaker !== b.speaker) return a.speaker.localeCompare(b.speaker);
    return a.text.localeCompare(b.text);
  });
  return lines;
}

export function formatTranscriptTxt(header: TranscriptHeader, lines: TranscriptLine[]): string {
  const headerLines: string[] = [];
  headerLines.push('# Verbio session transcript');
  headerLines.push(`# Session: ${header.livekitRoomName} (${header.sessionId})`);
  if (header.actualStart !== null) {
    headerLines.push(`# Started: ${header.actualStart.toISOString()}`);
  }
  if (header.actualEnd !== null) {
    headerLines.push(`# Ended: ${header.actualEnd.toISOString()}`);
  }
  if (header.participantNames.length > 0) {
    headerLines.push(`# Participants: ${header.participantNames.join(', ')}`);
  }

  if (lines.length === 0) {
    // No body → no trailing newline, no separator blank line. The
    // header alone is the whole file.
    return headerLines.join('\n');
  }

  const bodyLines = lines.map((line) => {
    // Collapse internal newlines so each cue stays on one line — the
    // .txt form is for human reading and one-line-per-turn keeps it
    // grep-friendly.
    const flattened = line.text.replace(/\s*\n\s*/g, ' ');
    return `[${formatClockTime(line.startMs)}] ${line.speaker}: ${flattened}`;
  });
  return [...headerLines, '', ...bodyLines].join('\n') + '\n';
}

export function formatTranscriptVtt(header: TranscriptHeader, lines: TranscriptLine[]): string {
  const out: string[] = [];
  out.push('WEBVTT');
  out.push('');
  out.push(`NOTE Verbio session ${header.sessionId} (${header.livekitRoomName})`);
  if (header.actualStart !== null) {
    out.push(`NOTE Started ${header.actualStart.toISOString()}`);
  }
  out.push('');

  lines.forEach((line, idx) => {
    out.push((idx + 1).toString());
    out.push(`${formatVttTime(line.startMs)} --> ${formatVttTime(line.endMs)}`);
    out.push(`<v ${escapeVoiceTag(line.speaker)}>${escapeVttBody(line.text)}`);
    out.push('');
  });
  return out.join('\n');
}

function estimateModeratorDurationMs(text: string): number {
  const wordCount = text.split(/\s+/).filter((w) => w !== '').length;
  if (wordCount === 0) return MODERATOR_DEFAULT_DURATION_MS;
  const estimateMs = Math.round((wordCount / MODERATOR_WPM) * 60_000);
  if (estimateMs < MODERATOR_MIN_DURATION_MS) return MODERATOR_MIN_DURATION_MS;
  if (estimateMs > MODERATOR_MAX_DURATION_MS) return MODERATOR_MAX_DURATION_MS;
  return estimateMs;
}

function formatClockTime(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  return `${pad2(h)}:${pad2(m)}:${pad2(s)}`;
}

function formatVttTime(ms: number): string {
  const safeMs = Math.max(0, Math.floor(ms));
  const totalSec = Math.floor(safeMs / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const millis = safeMs % 1000;
  return `${pad2(h)}:${pad2(m)}:${pad2(s)}.${pad3(millis)}`;
}

function pad2(n: number): string {
  return n.toString().padStart(2, '0');
}

function pad3(n: number): string {
  return n.toString().padStart(3, '0');
}

function escapeVoiceTag(name: string): string {
  // The voice tag's value is bounded by `>` — strip any embedded `>`
  // and trim. Display names from the DB already pass a length cap, so
  // a stripped-down version still uniquely identifies the speaker.
  return name.replace(/[<>]/g, '').trim();
}

function escapeVttBody(text: string): string {
  // VTT cue text: `&` and `<` are the only chars that need entities.
  // Newlines are legal inside a cue (each newline breaks to a new
  // sub-line) — collapse to a single space so we don't fragment the
  // cue across multiple visual rows for transcript display.
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/\s*\n\s*/g, ' ');
}
