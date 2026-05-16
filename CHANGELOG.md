# Changelog

All notable changes to Verbio will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Phases referenced below correspond to §14 of `verbio-engineering-brief.md`.

---

## [Unreleased]

### Added

- Initial repository setup: proprietary license (Viral Ventures LLC), git remote, root meta files (`.gitignore`, `.gitattributes`, `.editorconfig`, version pins), project docs (`README`, `CONTRIBUTING`, `SECURITY`), env template.
- Monorepo tooling: pnpm workspace, Turborepo, base `tsconfig`, Prettier, ESLint flat config, commitlint, lefthook git hooks.
- CI: GitHub Actions workflows for web, engine, shared-types up-to-date check, format check, and commit lint.
- GitHub project metadata: CODEOWNERS, PR template, issue templates, Dependabot config.
- Documentation scaffold: `docs/architecture.md`, `docs/runbook.md`, `docs/rules-reference.md`, ADR template, ADR-0001 (record architecture decisions), ADR-0002 (Railway over Supabase).
- VSCode workspace settings + recommended extensions.

### Changed

- **Stack pivot (pre-Phase-0):** dropped Supabase entirely. Final hosting split — **web on Vercel**, **engine on Railway** (Dockerfile), **Postgres + Redis on Railway** (managed addons), **recording storage on Cloudflare R2**. Auth is Auth.js v5 with the Postgres adapter + Resend magic-link; browser realtime is SSE backed by Redis pub/sub in the Next.js route layer (Vercel function `maxDuration: 300` with `last-event-id` reconnect + Postgres backfill); schema migrations are Alembic (source of truth) with Prisma introspecting the result via `prisma db pull`. Tenant isolation moves from RLS to an app-layer `scopedDb(orgId)` helper. Rationale and tradeoffs captured in [`docs/adr/0002-stack.md`](./docs/adr/0002-stack.md).

---

## Release notes format

Each release entry should include:

- **Phase:** which brief phase (§14) this release completes.
- **Added / Changed / Deprecated / Removed / Fixed / Security:** as applicable.
- **Migration notes:** schema changes, env var changes, breaking shared-type changes.
- **Operational notes:** anything the on-call should know.
