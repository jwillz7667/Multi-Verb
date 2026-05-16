<!--
Thanks for opening a PR. Keep it small enough that a reviewer can load it and grok the change end-to-end in under 30 minutes. If it's larger, split it.
-->

## Summary

<!-- 1–3 sentences. What changed and why. -->

## Phase reference

<!--
Reference brief §14, e.g.:
"Phase 2 — State engine: completes the 'state snapshots persisted to Postgres every tick' criterion."
-->

## Changes

- [ ]
- [ ]
- [ ]

## Test plan

- [ ] Unit tests pass (`pnpm test` / `uv run pytest`)
- [ ] Typecheck passes (`pnpm typecheck` / `uv run mypy --strict verbio_engine`)
- [ ] Lint passes (`pnpm lint` / `uv run ruff check`)
- [ ] Format clean (`pnpm format:check` / `uv run ruff format --check`)
- [ ] Manual verification: <describe>

## Quality gates

- [ ] No `any` in TS without inline justification comment
- [ ] No `dict`-typed payloads crossing Python module boundaries — Pydantic models on every seam
- [ ] New rules / decisions / commands match brief §5 / §7 schemas
- [ ] If schemas changed: ran `pnpm shared-types:generate` and committed generated TS
- [ ] If schema changed: Alembic migration added under `infra/postgres/migrations/` (forward-only) and `pnpm db:pull` ran to refresh `apps/web/prisma/schema.prisma`
- [ ] Architecture-significant change: ADR added under `docs/adr/`
- [ ] No secret-shaped strings committed (lefthook scan passed)

## Product principle check

- [ ] **Bias toward silence** preserved or strengthened (no new chatty paths)
- [ ] **Auditability** preserved — every decision (including `stay_silent`) is still fully logged

## Reviewer notes

<!-- Anything reviewers should pay particular attention to: tricky logic, perf hotspots, intentional design tradeoffs. -->

## Screenshots / recordings

<!-- For UI changes only. Delete this section otherwise. -->
