'use client';

/**
 * Replay export panel — surfaces every download a researcher needs to
 * leave the dashboard with the session in hand (brief §11.2):
 *
 *   - Transcript: .txt (human read) + .vtt (player overlay).
 *   - Decision log (.csv), state snapshots (.jsonl), flagged audio
 *     clips (.mp3) — placeholders pinned until L11–L13 land.
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

interface Props {
  sessionId: string;
}

interface ExportItem {
  label: string;
  href: string | null;
  testid: string;
  // A short hint shown beside disabled rows so a researcher who scans
  // the panel knows when the missing exports are slated to land.
  pendingHint?: string;
}

export function ReplayExports({ sessionId }: Props): React.ReactElement {
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
      href: null,
      testid: 'replay-export-decisions',
      pendingHint: 'P6 L11',
    },
    {
      label: 'State snapshots (.jsonl)',
      href: null,
      testid: 'replay-export-snapshots',
      pendingHint: 'P6 L12',
    },
    {
      label: 'Flagged clips (.mp3)',
      href: null,
      testid: 'replay-export-clips',
      pendingHint: 'P6 L13',
    },
  ];

  return (
    <section
      className="border-border-default bg-surface-primary flex flex-wrap items-center gap-3 rounded-lg border px-6 py-4"
      data-testid="replay-exports-slot"
    >
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
    </section>
  );
}
