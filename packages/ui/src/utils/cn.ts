import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Merge class names with Tailwind-aware conflict resolution.
 *
 * `clsx` handles conditional/array/object inputs; `twMerge` deduplicates
 * conflicting Tailwind utilities so the last-applied class always wins.
 * This is the standard shadcn/ui idiom and is exported here so every
 * Verbio surface uses one implementation.
 *
 * @example
 *   cn('px-2 py-1', condition && 'px-4') // → 'py-1 px-4'
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
