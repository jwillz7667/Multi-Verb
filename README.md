# Verbio

Real-time AI moderator for multi-participant qualitative research sessions.

> **Status:** Pre-Phase-0 scaffolding — see `verbio-engineering-brief.md` §14.
> **Canonical spec:** [`verbio-engineering-brief.md`](./verbio-engineering-brief.md) — read this first.

---

## What Verbio is

Verbio joins a voice call with up to 5 participants (plus observing researchers), listens to everyone, and intervenes sparingly to keep the conversation productive — inviting quiet participants in, redirecting topic drift, suggesting turn-taking when cross-talk gets out of hand, and summarizing when useful. Two non-negotiable product properties shape every design decision:

1. **The moderator is biased toward silence.** Default action every tick is `stay_silent`. The moderator must earn every word.
2. **Every decision is auditable.** Researchers must answer *"why did the moderator say that?"* **and** *"why didn't it say something here?"* in seconds.

The load-bearing architectural commitment that delivers both: **separate the deterministic, rule-based, fully-logged decision logic from the LLM-driven language generation.** The LLM is a mouth, not a brain.

---

## Repository layout

```
verbio/
├── apps/
│   └── web/                    # Next.js dashboard (researcher live + replay UI)
├── services/
│   └── engine/                 # Python engine (LiveKit agent + tick loop + rules + mouth)
├── packages/
│   ├── shared-types/           # Pydantic → JSON Schema → TS types (generated)
│   ├── ui/                     # Shared React components
│   └── eslint-config/          # Shared lint config
├── schemas/                    # Pydantic models — source of truth for shared types
├── infra/
│   ├── postgres/migrations/    # Alembic migrations (source of truth)
│   ├── railway/                # railway.toml + per-service definitions
│   ├── r2/                     # Cloudflare R2 lifecycle policies
│   └── docker-compose.dev.yml  # Local Postgres + Redis for development
├── docs/
│   ├── architecture.md         # Topology + service boundaries
│   ├── rules-reference.md      # V1 rule catalog
│   ├── runbook.md              # Operational playbook
│   └── adr/                    # Architecture Decision Records
└── verbio-engineering-brief.md # CANONICAL SPEC (read first)
```

The repo is a monorepo: **pnpm workspaces + Turborepo** for the TypeScript side, **uv** for the Python engine. The two services never share runtime code — they communicate via Redis (commands + events) and Postgres (state, decisions, replay).

---

## Stack

| Layer | Choice |
|---|---|
| Web | Next.js (TypeScript strict, App Router), Auth.js v5, Prisma, React, SSE for live updates; deployed to Vercel |
| Engine | Python 3.12, LiveKit Agents SDK, FastAPI (health/admin), SQLAlchemy 2.0 async + asyncpg; one process per active session; deployed to Railway (Dockerfile) |
| Data | Railway Postgres (pgBouncer, nightly backups); Railway Redis (command streams + event pub/sub); Cloudflare R2 (recording + export storage) |
| Auth + email | Auth.js v5 with Postgres adapter; Resend for transactional + magic-link delivery |
| AI — STT | Deepgram Nova-3 (streaming, per-track) |
| AI — Reasoning | Deterministic rules engine, **no LLM in the decision path** |
| AI — Mouth | Anthropic Claude Haiku (latest); Sonnet upgrade per study |
| TTS | Cartesia Sonic (primary), ElevenLabs Flash (fallback) |
| Voice infra | LiveKit Cloud (SFU) |
| Observability | OpenTelemetry traces, structured JSON logs, Sentry |
| CI | GitHub Actions |

---

## Getting started

> ⚠️ Phase 0 has not yet landed. Setup commands below describe the **target** workflow once scaffolding is complete.

### Prerequisites

- **Node** 22 LTS (`.nvmrc` pins this)
- **pnpm** 10+ (`packageManager` in `package.json` pins this)
- **Python** 3.12 (`.python-version` pins this)
- **uv** ≥ 0.5 (Python package + venv manager)
- **Docker** (for local Postgres + Redis via `docker compose` and for engine image builds)
- **Railway CLI** (optional, for `railway run` against the engine + data env)
- **Vercel CLI** (optional, for `vercel link` + `vercel env pull` to mirror the web env locally)
- **`uv`** ≥ 0.5 (Python package + venv manager — pins `services/engine` deps)

### First-time setup

```bash
# Install JS deps for the whole monorepo
pnpm install

# Install Python deps for the engine
cd services/engine && uv sync --all-extras && cd -

# Copy env templates (never commit real .env files)
cp .env.example .env.local

# Bring up local Postgres + Redis (matches Railway managed addons)
docker compose -f infra/docker-compose.dev.yml up -d

# Apply schema migrations (Alembic — source of truth) and refresh the Prisma client
pnpm db:migrate                # runs `alembic upgrade head` against $DATABASE_URL
pnpm db:pull                   # regenerates apps/web/prisma/schema.prisma from the live schema
```

### Daily commands

```bash
pnpm dev                       # web + engine in parallel with hot reload
pnpm lint                      # ESLint + ruff (per workspace, via turbo)
pnpm typecheck                 # tsc + mypy --strict
pnpm test                      # vitest + pytest
pnpm build                     # production builds
pnpm shared-types:generate     # Pydantic → JSON Schema → TS
```

---

## Quality gates

These are non-negotiable. Every PR must pass them; CI enforces.

- **TypeScript:** strict mode, no `any` without inline justification, ESLint `recommended-type-checked`.
- **Python:** `ruff check` + `ruff format --check` + `mypy --strict`. Pydantic at every boundary. No `dict`-typed payloads across module seams.
- **Tests:** ≥ 85% line coverage on `services/engine/verbio_engine/rules/` and `tick_loop.py`. Property-based tests on state math (Hypothesis).
- **Shared types:** `pnpm shared-types:generate` is idempotent; CI fails if regenerated TS differs from committed.
- **Conventional Commits** enforced by commitlint; lefthook runs pre-commit lint + format.
- **No `--no-verify` commits.** Hooks exist for a reason.

---

## Documentation

- **[Engineering brief](./verbio-engineering-brief.md)** — product + technical canonical spec
- **[Architecture](./docs/architecture.md)** — topology, service boundaries, data flow
- **[Rules reference](./docs/rules-reference.md)** — V1 rule catalog
- **[Runbook](./docs/runbook.md)** — operational playbook
- **[ADRs](./docs/adr/)** — architecture decision records
- **[Contributing](./CONTRIBUTING.md)** — workflow, commit conventions, review expectations
- **[Security](./SECURITY.md)** — vulnerability disclosure
- **[Changelog](./CHANGELOG.md)** — release notes

---

## License

Proprietary. Copyright © 2026 Viral Ventures LLC, Maple Grove, Minnesota. All rights reserved. See [LICENSE](./LICENSE).

Verbio is internal software. Unauthorized copying, modification, distribution, or use is strictly prohibited. For licensing inquiries, contact Viral Ventures LLC.
