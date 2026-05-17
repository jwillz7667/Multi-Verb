'use client';

/**
 * Replay export panel — surfaces every download a researcher needs to
 * leave the dashboard with the session in hand (brief §11.2):
 *
 *   - Transcript: .txt (human read) + .vtt (player overlay).
 *   - Decision log (.csv), state snapshots (.jsonl).
 *   - Flagged audio clips (.mp3): one anchor per `session_flags` row,
 *     cut from the composite recording.
 *
 * Each enabled download is a plain anchor tag pointed at the export
 * route. Browsers handle Content-Disposition: attachment natively, so
 * we don't need to wire fetch + Blob.createObjectURL to trigger a
 * save dialog. Anchors also give the researcher right-click → "save
 * link as" and "open in new tab" semantics for free.
 *
 * The disabled placeholders intentionally render at the same physical
 * footprint as the enabled buttons so the panel doesn't reflow as
 * later layers light them up.
 */

import Link from 'next/link';

import type { ReplayFlagDTO } from './replay-types';

interface Props {
  sessionId: string;
  flags: readonly ReplayFlagDTO[];
  /**
   * True iff the composite recording has been written by the egress
   * webhook. The clip route 422s when this is false; we surface it
   * client-side so the panel disables the buttons up-front instead of
   * letting the researcher click into a guaranteed error.
   */
  hasRecording: boolean;
}

interface ExportItem {
  label: string;
  href: string | null;
  testid: string;
  // A short hint shown beside disabled rows so a researcher who scans
  // the panel knows why the export is unavailable.
  pendingHint?: string;
}

export function ReplayExports({ sessionId, flags, hasRecording }: Props): React.ReactElement {
  const items: ExportItem[] = [
    {
      label: 'Transcript (.txt)',
      href: `/api/sessions/${sessionId}/exports/transcript?format=txt`,
      testid: 'replay-export-transcript-txt',
    },
    {
      label: 'Transcript (.vtt)',
      href: `/api/sessions/${sessionId}/exports/transcript?format=vtt`,
      testid: 'replay-export-transcript-vtt',
    },
    {
      label: 'Decision log (.csv)',
      href: `/api/sessions/${sessionId}/exports/decisions`,
      testid: 'replay-export-decisions',
    },
    {
      label: 'State snapshots (.jsonl)',
      href: `/api/sessions/${sessionId}/exports/snapshots`,
      testid: 'replay-export-snapshots',
    },
  ];

  return (
    <section
      className="border-border-default bg-surface-primary flex flex-col gap-3 rounded-lg border px-6 py-4"
      data-testid="replay-exports-slot"
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-text-primary text-sm font-medium">Export</span>
        {items.map((item) =>
          item.href !== null ? (
            <Link
              key={item.label}
              href={item.href}
              // `download` lets the browser respect Content-Disposition
              // without navigating away — the user stays on the replay
              // page. The empty value defers the filename choice to the
              // server header.
              download=""
              className="border-border-default text-text-primary hover:bg-surface-secondary rounded-md border px-3 py-1 text-xs"
              data-testid={item.testid}
            >
              {item.label}
            </Link>
          ) : (
            <button
              key={item.label}
              type="button"
              className="border-border-default text-text-tertiary cursor-not-allowed rounded-md border px-3 py-1 text-xs"
              disabled
              data-testid={item.testid}
            >
              {item.label}
              {item.pendingHint !== undefined && (
                <span className="text-text-tertiary ml-1 text-[10px]">({item.pendingHint})</span>
              )}
            </button>
          ),
        )}
      </div>

      <FlagClipsRow sessionId={sessionId} flags={flags} hasRecording={hasRecording} />
    </section>
  );
}

interface FlagClipsRowProps {
  sessionId: string;
  flags: readonly ReplayFlagDTO[];
  hasRecording: boolean;
}

/**
 * Renders the per-flag .mp3 download row. Falls back to a single
 * disabled placeholder when there's nothing to download yet:
 *
 *   - no recording → "(recording pending)" — egress hasn't completed,
 *   - no flags     → "(no flags)"          — researcher hasn't bookmarked.
 *
 * Each enabled anchor is centered on a flag's ts, with the timestamp
 * surfaced in the label so a researcher with many flags can pick the
 * right one by sight rather than by `data-testid` order alone.
 */
function FlagClipsRow({ sessionId, flags, hasRecording }: FlagClipsRowProps): React.ReactElement {
  return (
    <div className="flex flex-wrap items-center gap-3" data-testid="replay-export-clips-row">
      <span className="text-text-secondary text-xs font-medium">Flagged clips (.mp3)</span>
      {(() => {
        if (!hasRecording) {
          return (
            <button
              type="button"
              className="border-border-default text-text-tertiary cursor-not-allowed rounded-md border px-3 py-1 text-xs"
              disabled
              data-testid="replay-export-clips-placeholder"
            >
              No clips yet
              <span className="text-text-tertiary ml-1 text-[10px]">(recording pending)</span>
            </button>
          );
        }
        if (flags.length === 0) {
          return (
            <button
              type="button"
              className="border-border-default text-text-tertiary cursor-not-allowed rounded-md border px-3 py-1 text-xs"
              disabled
              data-testid="replay-export-clips-placeholder"
            >
              No clips yet
              <span className="text-text-tertiary ml-1 text-[10px]">(no flags)</span>
            </button>
          );
        }
        return flags.map((flag, idx) => (
          <Link
            key={flag.id}
            href={`/api/sessions/${sessionId}/exports/clips/${flag.id}`}
            download=""
            className="border-border-default text-text-primary hover:bg-surface-secondary rounded-md border px-3 py-1 text-xs"
            data-testid={`replay-export-clip-${String(idx)}`}
            // Title (tooltip) carries the note so the bare timestamp
            // label stays scannable but the researcher can hover for
            // the bookmark context they themselves typed.
            title={flag.note ?? formatFlagTs(flag.ts)}
          >
            Clip @ {formatFlagTs(flag.ts)}
          </Link>
        ));
      })()}
    </div>
  );
}

function formatFlagTs(isoTs: string): string {
  // 24h HH:MM:SS in the browser's local zone — same convention the
  // timeline + transcript panels use. Deliberately not UTC: researchers
  // think about "when in their session" not "when in Zulu".
  const d = new Date(isoTs);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}
