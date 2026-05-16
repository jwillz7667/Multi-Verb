import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { VisuallyHidden } from './VisuallyHidden.js';

describe('VisuallyHidden', () => {
  it('renders children as a span', () => {
    render(<VisuallyHidden>Skip to content</VisuallyHidden>);

    const node = screen.getByText('Skip to content');
    expect(node.tagName).toBe('SPAN');
  });

  it('applies the sr-only clip styles', () => {
    render(<VisuallyHidden>announcer</VisuallyHidden>);

    const node = screen.getByText('announcer');
    expect(node.className).toContain('absolute');
    expect(node.className).toContain('h-px');
    expect(node.className).toContain('w-px');
    expect(node.className).toContain('overflow-hidden');
  });

  it('merges user className without dropping the sr-only utilities', () => {
    render(<VisuallyHidden className="text-red-500">x</VisuallyHidden>);

    const node = screen.getByText('x');
    expect(node.className).toContain('text-red-500');
    expect(node.className).toContain('absolute');
  });

  it('forwards arbitrary HTML attributes', () => {
    render(
      <VisuallyHidden id="live-region" role="status">
        pinged
      </VisuallyHidden>,
    );

    const node = screen.getByRole('status');
    expect(node).toHaveAttribute('id', 'live-region');
  });
});
