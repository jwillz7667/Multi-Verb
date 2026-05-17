/**
 * Tests for the replay export panel.
 *
 * Pins:
 *   - the transcript .txt + .vtt links are real anchors pointed at the
 *     export route with the correct `format` query (browser downloads
 *     depend on this being a navigable URL with `download=""`),
 *   - the decision log .csv and state snapshots .jsonl downloads work,
 *   - flagged clips render one anchor per flag when a recording is
 *     available; otherwise a disabled placeholder with the right hint,
 *   - the wrapping section keeps the `replay-exports-slot` testid the
 *     shell smoke test relies on.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ReplayExports } from './replay-exports';

import type { ReplayFlagDTO } from './replay-types';

function makeFlag(overrides: Partial<ReplayFlagDTO> = {}): ReplayFlagDTO {
  return {
    id: 'flag-default-id',
    ts: '2026-05-01T10:00:00.000Z',
    researcher_id: 'r-1',
    note: null,
    auto_generated: false,
    ...overrides,
  };
}

describe('<ReplayExports />', () => {
  it('exposes the .txt transcript download with the right href + download attr', () => {
    render(<ReplayExports sessionId="sess-abc" flags={[]} hasRecording={false} />);

    const link = screen.getByTestId('replay-export-transcript-txt');
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', '/api/sessions/sess-abc/exports/transcript?format=txt');
    // Empty value defers the filename choice to the server's
    // Content-Disposition header; presence of the attribute is what
    // matters for the browser to treat the click as a save instead of
    // a navigation.
    expect(link).toHaveAttribute('download', '');
    expect(link).toHaveTextContent(/Transcript \(\.txt\)/);
  });

  it('exposes the .vtt transcript download with the right href + download attr', () => {
    render(<ReplayExports sessionId="sess-abc" flags={[]} hasRecording={false} />);

    const link = screen.getByTestId('replay-export-transcript-vtt');
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', '/api/sessions/sess-abc/exports/transcript?format=vtt');
    expect(link).toHaveAttribute('download', '');
    expect(link).toHaveTextContent(/Transcript \(\.vtt\)/);
  });

  it('exposes the decision-log .csv download with the right href + download attr', () => {
    render(<ReplayExports sessionId="sess-abc" flags={[]} hasRecording={false} />);

    const link = screen.getByTestId('replay-export-decisions');
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', '/api/sessions/sess-abc/exports/decisions');
    expect(link).toHaveAttribute('download', '');
    expect(link).toHaveTextContent(/Decision log \(\.csv\)/);
  });

  it('exposes the state-snapshots .jsonl download with the right href + download attr', () => {
    render(<ReplayExports sessionId="sess-abc" flags={[]} hasRecording={false} />);

    const link = screen.getByTestId('replay-export-snapshots');
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', '/api/sessions/sess-abc/exports/snapshots');
    expect(link).toHaveAttribute('download', '');
    expect(link).toHaveTextContent(/State snapshots \(\.jsonl\)/);
  });

  it('renders a disabled "(recording pending)" placeholder when the composite has not landed', () => {
    render(
      <ReplayExports
        sessionId="sess-abc"
        flags={[makeFlag({ id: 'flag-1' })]}
        hasRecording={false}
      />,
    );

    const placeholder = screen.getByTestId('replay-export-clips-placeholder');
    expect(placeholder.tagName).toBe('BUTTON');
    expect(placeholder).toBeDisabled();
    expect(placeholder).toHaveTextContent(/recording pending/);
    // No per-flag links rendered while the recording is missing.
    expect(screen.queryByTestId('replay-export-clip-0')).toBeNull();
  });

  it('renders a disabled "(no flags)" placeholder when the recording is ready but no flags exist', () => {
    render(<ReplayExports sessionId="sess-abc" flags={[]} hasRecording={true} />);

    const placeholder = screen.getByTestId('replay-export-clips-placeholder');
    expect(placeholder).toBeDisabled();
    expect(placeholder).toHaveTextContent(/no flags/);
  });

  it('renders one downloadable .mp3 link per flag when both flags and a recording exist', () => {
    const flags = [
      makeFlag({ id: 'flag-a', ts: '2026-05-01T10:02:30.000Z', note: 'great quote' }),
      makeFlag({ id: 'flag-b', ts: '2026-05-01T10:08:15.000Z', note: null }),
    ];
    render(<ReplayExports sessionId="sess-abc" flags={flags} hasRecording={true} />);

    expect(screen.queryByTestId('replay-export-clips-placeholder')).toBeNull();

    const linkA = screen.getByTestId('replay-export-clip-0');
    expect(linkA.tagName).toBe('A');
    expect(linkA).toHaveAttribute('href', '/api/sessions/sess-abc/exports/clips/flag-a');
    expect(linkA).toHaveAttribute('download', '');
    expect(linkA).toHaveAttribute('title', 'great quote');

    const linkB = screen.getByTestId('replay-export-clip-1');
    expect(linkB).toHaveAttribute('href', '/api/sessions/sess-abc/exports/clips/flag-b');
    // No note → title falls back to the formatted timestamp so the
    // anchor still has *something* a screen reader can announce.
    expect(linkB).toHaveAttribute('title');
    expect(linkB.getAttribute('title')).not.toBe('');
  });

  it('keeps the `replay-exports-slot` testid the shell smoke test asserts', () => {
    render(<ReplayExports sessionId="sess-abc" flags={[]} hasRecording={false} />);

    expect(screen.getByTestId('replay-exports-slot')).toBeInTheDocument();
  });

  it('encodes the session id into each download URL verbatim', () => {
    render(
      <ReplayExports
        sessionId="some-other-session"
        flags={[makeFlag({ id: 'flag-x' })]}
        hasRecording={true}
      />,
    );

    expect(screen.getByTestId('replay-export-transcript-txt')).toHaveAttribute(
      'href',
      '/api/sessions/some-other-session/exports/transcript?format=txt',
    );
    expect(screen.getByTestId('replay-export-transcript-vtt')).toHaveAttribute(
      'href',
      '/api/sessions/some-other-session/exports/transcript?format=vtt',
    );
    expect(screen.getByTestId('replay-export-snapshots')).toHaveAttribute(
      'href',
      '/api/sessions/some-other-session/exports/snapshots',
    );
    expect(screen.getByTestId('replay-export-clip-0')).toHaveAttribute(
      'href',
      '/api/sessions/some-other-session/exports/clips/flag-x',
    );
  });
});
