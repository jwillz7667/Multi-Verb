# Contributing to Verbio

Verbio is proprietary software owned by Viral Ventures LLC. **External contributions are not accepted.** This document is for internal contributors with authorized access.

---

## 1. Read the brief first

[`verbio-engineering-brief.md`](./verbio-engineering-brief.md) is the canonical specification. Every change must be consistent with it. If the brief is wrong or incomplete for a real situation, propose a brief revision _first_, in a separate PR, before writing code that contradicts the brief.

If you are not sure whether a change fits the spec, **stop and ask** — flexibility in the early phases is cheap, drift from the spec is expensive.

---

## 2. Phased delivery

We follow the phased plan in §14 of the brief. **Do not skip phases. Do not bundle phases.** Each phase ends with a tagged release. Within a phase, ship small reviewable PRs — under 30 minutes of review.

The two product principles (`bias toward silence` and `every decision auditable`) come from delivering each phase honestly. Phase 3 (shadow mode) exists specifically so researchers can validate ≥ 70% agreement with would-be interventions **before** Phase 4 lets the moderator speak. That sequencing is the safety story — do not shortcut it.

---

## 3. Branches

- `main` is always deployable.
- Feature branches: `feat/<short-slug>`
- Bug fixes: `fix/<ticket-or-slug>`
- Refactors: `refactor/<slug>`
- Chores: `chore/<slug>`

Branch from `main`, rebase before merging. Squash merges only — keep `main` linear.

---

## 4. Commit conventions

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), enforced by commitlint:

| Type        | Use for                             |
| ----------- | ----------------------------------- |
| `feat:`     | New functionality                   |
| `fix:`      | Bug fix                             |
| `refactor:` | Restructure with no behavior change |
| `perf:`     | Performance improvement             |
| `test:`     | Adding/updating tests               |
| `docs:`     | Documentation only                  |
| `chore:`    | Tooling, dependencies, infra        |
| `build:`    | Build system changes                |
| `ci:`       | CI configuration changes            |

Scope is optional but encouraged: `feat(rules): add silence_gap rule`.

**One logical change per commit.** Refactors and behavior changes never share a commit. Imperative subject, ≤ 72 chars. Body explains _why_, not _what_.

---

## 5. Quality gates (run locally before pushing)

```bash
# JS / TS
pnpm lint
pnpm typecheck
pnpm test
pnpm format:check

# Python (engine)
cd services/engine
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict verbio_engine
uv run pytest

# If you touched Pydantic schemas:
pnpm shared-types:generate
git diff --exit-code packages/shared-types/src/generated   # must be clean
```

CI runs all of the above. **Red CI blocks merge — no exceptions, no `--no-verify`.** If a hook fails, fix the underlying issue. If a hook is wrong, fix the hook in a separate PR.

---

## 6. Code review

- All PRs require ≥ 1 approving review from a CODEOWNER (see `.github/CODEOWNERS`).
- A reviewer should be able to load the PR and understand the change end-to-end in **under 30 minutes**. If it would take longer, split the PR.
- Reviewers should verify the change preserves the two product principles. If a change weakens either, request changes regardless of how clean the code looks.

---

## 7. Architectural changes

If a change:

- crosses service boundaries,
- modifies the shared domain model (`schemas/`),
- alters a quality gate, or
- introduces a new third-party dependency at runtime,

… write an ADR under [`docs/adr/`](./docs/adr/). Reference the ADR number in the PR description.

---

## 8. Schema and migration changes

- All Postgres schema changes via Alembic under `infra/postgres/migrations/`: `cd services/engine && uv run alembic revision -m "<slug>" --autogenerate` then `uv run alembic upgrade head`. Regenerate the web Prisma client via `pnpm db:pull`; commit the updated `apps/web/prisma/schema.prisma` in the same PR. CI fails if the committed Prisma schema drifts from the live database.
- All shared-type changes via Pydantic models in `schemas/`. Regenerate TS types: `pnpm shared-types:generate`.
- Migrations are **forward-only**. If you need to revert, write a new migration.
- Never edit a migration after it has merged to `main`.

---

## 9. Testing expectations

- **Engine — rules:** table-driven unit tests for every rule, covering fire and no-fire cases, plus edge cases (cooldown, missing data, malformed state).
- **Engine — tick loop:** integration tests with a fake clock + synthetic event streams.
- **Engine — state math:** property-based tests (Hypothesis) for invariants ("sum of speaking_time ≤ session elapsed time", etc.).
- **Web — components:** React Testing Library on the control bar, decision log, "Why quiet now?" panel.
- **Web — E2E:** Playwright for: start session → observe live decision → intervene manually → end session → open replay.
- **Coverage target:** ≥ 85% on `verbio_engine/rules/` and `verbio_engine/tick_loop.py`. Lower elsewhere is acceptable. Coverage is a smell-detector, not a goal.

---

## 10. Security and PII

Sessions handled by Verbio may contain PII and may fall under IRB review. When testing locally:

- **Never commit real session recordings, transcripts, or participant identifiers.**
- Use the synthetic fixtures under `services/engine/tests/fixtures/`.
- If you accidentally commit sensitive data, force-push removal is insufficient — rotate any exposed secrets and escalate per [SECURITY.md](./SECURITY.md).

---

## 11. Performance budgets

These are gates, not aspirations. CI enforces synthetic versions starting Phase 4.

- **End-of-rule-trigger → first audible word:** ≤ 1500 ms p95, ≤ 2500 ms p99.
- **Tick loop:** never blocks on LLM/TTS. If a tick exceeds budget, log `suppressed_by=["latency_exceeded"]` and continue.
- **Engine memory:** stable over a 60-min, 5-participant session under load.

---

## 12. Getting unstuck

When stuck on a non-trivial decision, the priority order:

1. Re-read the relevant section of the brief.
2. Check `docs/adr/` for prior decisions on the topic.
3. Ask in the team's engineering channel before writing speculative code.
4. If still ambiguous, write an ADR proposing the path forward and request review.

The brief is opinionated for a reason. When in doubt, follow the brief.
