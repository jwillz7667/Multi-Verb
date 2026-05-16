/**
 * @verbio/ui — shared React primitives + Tailwind helpers.
 *
 * This is the public surface. Consumers (currently just `apps/web`) import
 * from `@verbio/ui` only; deep imports into `src/components/` or
 * `src/utils/` are not part of the contract and will break without notice.
 */

export { cn } from './utils/cn.js';
export { VisuallyHidden } from './components/VisuallyHidden.js';
