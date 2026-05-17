/**
 * Tests for the replay export panel.
 *
 * Pins:
 *   - the transcript .txt + .vtt links are real anchors pointed at the
 *     export route with the correct `format` query (browser downloads
 *     depend on this being a navigable URL with `download=""`),
 *   - the placeholder button for L13 is visually present but disabled,
 *     with a "(P# L#)" hint so a researcher who scans the panel knows
 *     what's still pending,
 *   - the wrapping section keeps the `replay-exports-slot` testid the
 *     shell smoke test relies on.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ReplayExports } from './replay-exports';

describe('<ReplayExports />', () => {
  it('exposes the .txt transcript download with the right href + download attr', () => {
    render(<ReplayExports sessionId="sess-abc" />);

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
    render(<ReplayExports sessionId="sess-abc" />);

    const link = screen.getByTestId('replay-export-transcript-vtt');
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', '/api/sessions/sess-abc/exports/transcript?format=vtt');
    expect(link).toHaveAttribute('download', '');
    expect(link).toHaveTextContent(/Transcript \(\.vtt\)/);
  });

  it('exposes the decision-log .csv download with the right href + download attr', () => {
    render(<ReplayExports sessionId="sess-abc" />);

    const link = screen.getByTestId('replay-export-decisions');
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', '/api/sessions/sess-abc/exports/decisions');
    expect(link).toHaveAttribute('download', '');
    expect(link).toHaveTextContent(/Decision log \(\.csv\)/);
  });

  it('exposes the state-snapshots .jsonl download with the right href + download attr', () => {
    render(<ReplayExports sessionId="sess-abc" />);

    const link = screen.getByTestId('replay-export-snapshots');
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', '/api/sessions/sess-abc/exports/snapshots');
    expect(link).toHaveAttribute('download', '');
    expect(link).toHaveTextContent(/State snapshots \(\.jsonl\)/);
  });

  it('renders the disabled placeholder for the L13 clips export with a phase hint', () => {
    render(<ReplayExports sessionId="sess-abc" />);

    const clips = screen.getByTestId('replay-export-clips');
    expect(clips.tagName).toBe('BUTTON');
    expect(clips).toBeDisabled();
    expect(clips).toHaveTextContent(/Flagged clips \(\.mp3\)/);
    expect(clips).toHaveTextContent(/P6 L13/);
  });

  it('keeps the `replay-exports-slot` testid the shell smoke test asserts', () => {
    render(<ReplayExports sessionId="sess-abc" />);

    expect(screen.getByTestId('replay-exports-slot')).toBeInTheDocument();
  });

  it('encodes the session id into each download URL verbatim', () => {
    render(<ReplayExports sessionId="some-other-session" />);

    expect(screen.getByTestId('replay-export-transcript-txt')).toHaveAttribute(
      'href',
      '/api/sessions/some-other-session/exports/transcript?format=txt',
    );
    expect(screen.getByTestId('replay-export-transcript-vtt')).toHaveAttribute(
      'href',
      '/api/sessions/some-other-session/exports/transcript?format=vtt',
    );
  });
});
