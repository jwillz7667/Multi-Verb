# @verbio/ui

Shared React primitives and Tailwind helpers used across the Verbio
frontend. Today there is one consumer (`apps/web`), but the seam exists
so a future second surface (admin tool, marketing site, embeddable
widget) doesn't have to fork.

## What's here

- `cn(...inputs)` — Tailwind-aware `className` joiner. Wraps `clsx` +
  `tailwind-merge` so the last-applied utility wins on conflict. This
  is the standard shadcn/ui idiom; we hoist it here so every Verbio
  surface uses the same implementation.
- `<VisuallyHidden>` — accessibility primitive. Renders children
  off-screen via the sr-only clip pattern (still announced by screen
  readers, invisible to sighted users). Use for skip links, icon-only
  button labels, and live-region announcers.

## Conventions

- **One primary export per file**, named to match the file
  (`VisuallyHidden.tsx` → `VisuallyHidden`).
- **Public surface only via `src/index.ts`.** Deep imports (e.g.
  `@verbio/ui/components/VisuallyHidden`) are not part of the
  contract and may break without notice.
- **Tailwind utilities, not bespoke CSS.** This package ships no
  `.css` files. Tailwind is processed by the consumer (web app's
  Tailwind config must include `'node_modules/@verbio/ui/**'` in
  `content`).
- **No business state.** Components are presentational. Domain types
  live in `@verbio/shared-types`; data fetching belongs in the app.

## Why not `shadcn/ui` here?

shadcn/ui's model is **copy components into your app**, not install
them from npm. That is right for app-specific component code in
`apps/web/components/ui/`. The shared package is for primitives that
are obviously cross-app (`cn`, `VisuallyHidden`, future
`<LiveStatusBadge>`, etc.).

## Run

```
pnpm --filter @verbio/ui lint       # eslint
pnpm --filter @verbio/ui typecheck  # tsc --noEmit
pnpm --filter @verbio/ui test       # vitest run (jsdom + RTL)
```
