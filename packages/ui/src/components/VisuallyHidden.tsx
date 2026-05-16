import { type HTMLAttributes, forwardRef } from 'react';

import { cn } from '../utils/cn.js';

/**
 * Renders children visually hidden but available to assistive technology.
 *
 * Uses the canonical "sr-only" CSS pattern (clip + 1px) rather than
 * `display: none` / `visibility: hidden` so screen readers still announce
 * the content. Use for skip links, live-region announcers, or icon-only
 * buttons that need an accessible name.
 */
export const VisuallyHidden = forwardRef<HTMLSpanElement, HTMLAttributes<HTMLSpanElement>>(
  function VisuallyHidden({ className, children, ...rest }, ref) {
    return (
      <span
        ref={ref}
        className={cn(
          'absolute h-px w-px overflow-hidden whitespace-nowrap border-0 p-0',
          '[clip:rect(0,0,0,0)] [clip-path:inset(50%)]',
          className,
        )}
        {...rest}
      >
        {children}
      </span>
    );
  },
);
