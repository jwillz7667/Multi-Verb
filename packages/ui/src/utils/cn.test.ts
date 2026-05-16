import { describe, expect, it } from 'vitest';

import { cn } from './cn.js';

describe('cn', () => {
  it('joins string class names', () => {
    expect(cn('px-2', 'py-1')).toBe('px-2 py-1');
  });

  it('drops falsy values', () => {
    expect(cn('px-2', null, undefined, false && 'hidden')).toBe('px-2');
  });

  it('honors conditional object form', () => {
    expect(cn('base', { active: true, disabled: false })).toBe('base active');
  });

  it('resolves Tailwind conflicts with last-write-wins', () => {
    expect(cn('px-2 py-1', 'px-4')).toBe('py-1 px-4');
  });

  it('flattens nested arrays', () => {
    expect(cn(['px-2', ['py-1', 'text-sm']])).toBe('px-2 py-1 text-sm');
  });
});
