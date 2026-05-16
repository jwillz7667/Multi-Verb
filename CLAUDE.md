# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

This repo is **greenfield**. The only artifact is `verbio-engineering-brief.md` — a 778-line specification for **Verbio**, a real-time AI moderator for multi-participant qualitative research sessions (focus groups, user interviews). The brief is the canonical source of truth for product, architecture, data model, phased delivery, and quality gates. **Read it first** before making any change; it is not a sketch, it is the contract.

No `package.json`, `pyproject.toml`, migrations, services, or CI exist yet. The first PR is Phase 0 in its entirety (see §14 of the brief): repo scaffolding, both services running locally, CI green, the shared-types pipeline working end-to-end. No application code, no rules — just the bones.

Until Phase 0 lands, there are no build / lint / test commands to document. Once scaffolded, the brief mandates:

- **Web (`apps/web`):** pnpm + Turborepo, Next.js, ESLint with `@typescript-eslint/recommended-type-checked`, strict TS, Playwright E2E, React Testing Library components.
- **Engine (`services/engine`):** Python 3.12, `ruff` + `mypy --strict`, pytest with Hypothesis for state-math property tests, ≥ 85% line coverage on `verbio_engine/rules/` and `verbio_engine/tick_loop.py`.
- **CI:** GitHub Actions runs lint + typecheck + test + build on every PR. Red CI blocks merge.

When you add tooling, wire the corresponding `pnpm <script>` / `uv run <cmd>` invocations into this file under a Commands section.

## Two non-negotiable product principles

Every design decision must serve these. If a change weakens either, it is wrong regardless of how clean the code looks.

1. **The moderator is biased toward silence.** Default action every tick is `stay_silent`. The moderator must earn every word. Researchers chose this tool over a human moderator _because_ they want minimal contamination of group dynamics. The `QuietnessBudget` (§7.4) is the strongest expression of this and is live-adjustable mid-session.

2. **Every decision is auditable.** Researchers must answer "why did the moderator say that?" _and_ "why didn't it say something here?" in seconds. `stay_silent` decisions are persisted with the same fidelity as spoken ones, and every rule's evaluation is logged every tick whether it fired or not (`rule_evaluations` table). The audit trail is part of the product, not a debugging convenience.

## The load-bearing architectural commitment

**Separate the decision logic from the language generation.** The rules engine is deterministic, rule-based, fully logged, and chooses _whether_ to speak and _to whom_. The LLM ("mouth layer") only phrases what the engine already decided, in one sentence, with no scope to introduce topics or opinions. **The LLM is a mouth, not a brain.**

Code organization must reflect this. The mouth layer (§8) must never see full participant state, rule logic, or which rules fired — only a structured `ModeratorDecision` plus the minimal context needed to phrase it. Crossing this seam is the most likely way to corrode the product.

## High-level topology

Two services + shared types, deployed independently:

- **`verbio-web`** (Next.js, TypeScript, Vercel): dashboard, auth (Auth.js v5 + Resend), session lifecycle, replay, exports. Owns **no** real-time decision logic. Issues researcher commands to the engine via a Redis stream.
- **`verbio-engine`** (Python 3.12, LiveKit Agents SDK, FastAPI for health/admin, Railway): the brain. One process per active session, spawned by a thin supervisor. Owns the 2 Hz tick loop, `SessionState`, rules, decisions, LLM/TTS orchestration. Joins each LiveKit room as the moderator participant.
- **`verbio-shared`**: shared types for `ParticipantState`, `ModeratorDecision`, `RuleEvaluation`, `ResearcherCommand`. **Pydantic is the source of truth** → JSON Schema → TypeScript via `json-schema-to-typescript`. CI must fail if generated TS is stale. The two services drifting on these shapes is a P0 bug.

Transports:

- **Server-Sent Events from Next.js**, fed by **Redis pub/sub** on `verbio:events:{session_id}`, is how the dashboard observes live state. `EventSource` reconnects with `last-event-id`; the SSE route backfills missed rows from Postgres before resuming. Do not invent a custom WebSocket protocol.
- **Redis streams** are the command bus from web → engine.
- **LiveKit Cloud** is the SFU; participants connect there directly.
- **Cloudflare R2** holds mixed + per-participant recordings; signed URLs issued by Next.js with short TTLs.

## Tick loop (the heartbeat)

The engine runs at 2 Hz (500ms tick interval, configurable). Each tick is a pure function of state → decision, plus side effects (§6 of brief). Two invariants are absolute:

1. **Persistence before execution.** A crash mid-tick must never leave a spoken utterance with no decision record. Write the `decisions` row + `rule_evaluations` rows _first_, then call mouth → TTS → publish.
2. **The tick loop never blocks on LLM/TTS.** If they exceed the latency budget, log the decision as `was_executed=false` with `suppressed_by=["latency_exceeded"]` and the next tick proceeds. Latency cannot stall the loop.

**Latency budget:** end-of-rule-trigger to first audible word ≤ 1500ms p95, ≤ 2500ms p99. This is enforced as a synthetic CI perf test starting Phase 4 (§14). Mouth layer streams tokens to TTS as they arrive to hit it.

## Rules engine

Rules are first-class versioned objects (`Rule` protocol in §7.1). V1 ships seven rules (§7.2): `silence_gap`, `speaker_imbalance`, `topic_drift`, `cross_talk_pattern`, `unheard_participant`, `stalled_thread`, `time_remaining_pressure`. Each has a documented default cooldown, priority, and config object.

Resolution when multiple rules fire (§7.3): filter cooldown → filter quietness budget → sort by priority desc, confidence desc. Losers are logged with `suppressed_reason="lower_priority_won"`.

**Rule versioning is sacred.** Each session snapshots `rules_version` + config at start (`sessions.config_snapshot`). Replay must use the snapshotted version; never let a config change retroactively alter historical session interpretation. New rule? New version. Tweaked threshold? New version.

## Data layer

Railway Postgres. Schema in §10.1 of the brief; key tables: `studies`, `sessions`, `participants`, `utterances`, `state_snapshots`, `decisions`, `rule_evaluations`, `researcher_actions`, `session_flags`. **Tenant isolation is enforced at the application layer**, not via RLS (see brief §10.3 and ADR-0002). All web reads go through a `scopedDb(orgId)` helper that injects `where: { orgId }` on every query; direct `prisma.<model>` calls outside it are lint-banned. The engine validates inbound command `org_id` against the session's `org_id` before applying.

`state_snapshots` writes every tick (7200 rows per 60-min session). This is intentional; storage cost is trivial vs. analysis value. A retention job downsamples to 1 Hz after 30 days.

All schema changes via Alembic (`infra/postgres/migrations/`), reviewed in PR. The web Prisma client is regenerated via `prisma db pull` against the Alembic-managed schema; CI fails if `apps/web/prisma/schema.prisma` drifts from the live database.

## Phased delivery — don't skip ahead

The brief defines 8 phases (Phase 0–7, §14). Each phase produces a working, demoable artifact and has explicit "done when" gates. Do not bundle phases. Do not skip the shadow-mode phase (Phase 3) where the moderator stays silent while researchers validate ≥ 70% agreement with would-be interventions before Phase 4 lets it speak.

Each phase merges to main as one or more PRs, each PR reviewable in under 30 minutes, each phase ends with a tagged release. The phased plan is itself part of the quality bar — sequencing is how we earn confidence that the moderator's silence and the audit trail are trustworthy before the moderator speaks in production.

## Stack notes (this project overrides global defaults where they conflict)

The user's global `~/.claude/CLAUDE.md` defaults to Node/Fastify/Prisma for backend and OpenAI for AI. **This project's brief is authoritative and overrides those defaults:**

- Engine is **Python 3.12** (for LiveKit Agents SDK, Silero VAD, numpy state math, and rules-engine readability), not Node.
- Web is **Next.js** (not Fastify), deployed to **Vercel**. Engine is on **Railway** (Dockerfile). Web reaches Railway Postgres / Redis over public TLS via `DATABASE_URL_POOLED` (pgBouncer transaction mode on port 6543) — required because Vercel serverless functions can't share a private network with Railway.
- Postgres + Redis are **Railway-managed**, not Supabase. Web ORM is **Prisma** (introspected via `prisma db pull` from the Alembic-managed schema). Engine ORM is **SQLAlchemy 2.0 async + asyncpg**.
- Auth is **Auth.js v5** with the Postgres adapter + **Resend** magic-link, not Supabase Auth.
- Recording storage is **Cloudflare R2** (S3-compatible), not Supabase Storage.
- Browser realtime is **SSE backed by Redis pub/sub** in the Next.js route layer, not Supabase Realtime.
- Mouth LLM default is **Claude Haiku** (latest available) via Anthropic API, upgradeable to Sonnet per study. Not OpenAI.
- STT is **Deepgram** (matches global default).
- TTS default is **Cartesia Sonic**, fallback **ElevenLabs Flash**.

The user's other global rules (feature-first organization, strict layer direction, public API per module, typed errors, no `any`, etc.) all apply.

## What to do first

When picking up new work in this repo, in priority order:

1. If Phase 0 has not landed: build Phase 0 in its entirety as the first PR. Nothing else.
2. If a phase is in progress: check the brief's "Done when" for that phase, finish unmet criteria, do not start the next.
3. If unclear which phase is current: inspect `git log --oneline` and the repo structure, map against §14 of the brief, then ask.

Never invent rules, decision types, command types, or schema fields that aren't in the brief. If the brief is wrong or incomplete for a real situation, flag it explicitly and propose a brief revision before writing code.
