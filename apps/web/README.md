# @verbio/web

Verbio's research dashboard — Next.js 15 App Router on Vercel.

## What this service owns

- Researcher-facing dashboard: studies, sessions, live moderator control,
  replay, exports.
- Auth.js v5 + Resend magic-link sign-in (lands in next commit after this
  scaffold).
- SSE endpoint backed by Redis pub/sub for live decision streaming.
- Researcher command bus (web → engine via Redis stream).

What this service does **not** own: the moderator's decision logic. That
lives in `services/engine`. The web tier observes and commands; the
engine decides.

## Run

```bash
# install once at repo root
pnpm install

# dev server on http://localhost:3000
pnpm --filter @verbio/web dev

# checks
pnpm --filter @verbio/web typecheck
pnpm --filter @verbio/web lint
pnpm --filter @verbio/web test
pnpm --filter @verbio/web build
```

Health endpoints once running:

- `GET /api/health` — liveness (Node up, returns build metadata).
- `GET /api/ready` — readiness (Postgres + Redis + engine probes;
  Phase 0 reports each as `"skip"`).

## Environment

Copy `.env.example` → `.env.local` and fill in. The Zod schema in
`src/lib/env.ts` validates every variable at first import and throws
a descriptive error if anything is missing or malformed — failure is
immediate rather than surfacing as a `undefined.split is not a
function` in a request handler.

Server-only and client-safe vars are split: `NEXT_PUBLIC_*` flow
into the browser bundle, everything else stays on the server.

## Stack

- **Next.js 15** (App Router, React 19, RSC by default).
- **TypeScript** strict, no `any`, type-checked ESLint via
  `@verbio/eslint-config/base` + `/next`.
- **Tailwind CSS 4** with CSS-first config — design tokens live in
  `src/app/globals.css` under `@theme inline`, mirroring the handoff
  prototype's CSS variables exactly.
- **IBM Plex Sans / Mono** via `next/font/google`.
- **Vitest + React Testing Library + jsdom** for unit + component
  tests. **Playwright** for E2E (lands when the first user-facing
  flow does).
- **Zod** at every external boundary (env, API bodies, query strings).

## Phase 0 scope

This commit ships the scaffold only:

- Next.js boots, renders a `verbio` placeholder home page that
  links to the health endpoints.
- `/api/health` and `/api/ready` return real JSON with build info.
- Env validation works (proven by `src/lib/env.test.ts`).
- Vitest runs (proven by route + env tests).
- ESLint, TypeScript, and `next build` all pass clean.

The Auth.js v5 + Prisma + Postgres adapter wiring lands as a
separate commit (still inside Phase 0) so this scaffold can be
reviewed independently.

## Theme

Light + dark via `data-theme="dark"` on `<html>`. The handoff prototype
toggles this via a custom hook; this scaffold sets `light` as the
default. The full theme switcher lands with the sidebar component in
Phase 1.
