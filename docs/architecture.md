# Verbio — Architecture

> This document is the high-level architectural reference. It mirrors §4 of
> [`verbio-engineering-brief.md`](../verbio-engineering-brief.md) and exists so on-call,
> reviewers, and new joiners can orient in minutes without re-reading the full brief.
>
> When this doc and the brief disagree, **the brief wins**, and this doc is updated
> in the same PR.

---

## 1. Product principles that shape the architecture

Two non-negotiables — every design decision must serve them:

1. **The moderator is biased toward silence.** Default action every tick is `stay_silent`.
2. **Every decision is auditable.** Researchers must be able to answer _why did the moderator say that?_ **and** _why didn't it say something here?_ in seconds.

The load-bearing architectural commitment that delivers both:

> **Separate the deterministic, rule-based, fully-logged decision logic from the LLM-driven language generation.**
> The LLM is a mouth, not a brain.

Crossing that seam is the single most likely way to corrode the product. Reviewers should treat any change that gives the LLM influence over _whether_ or _to whom_ the moderator speaks as a P0 architectural regression.

---

## 2. High-level topology

```
┌─────────────────────────────────────────────────────────────┐
│                      Researcher Browser                     │
│         (Next.js dashboard — live control + replay)         │
└──────────────┬──────────────────────────────┬───────────────┘
               │   SSE (EventSource)          │ HTTPS
               │                              │
┌──────────────▼──────────────────────────────▼───────────────┐
│                       Next.js (Vercel)                      │
│   - Auth.js v5 (Postgres adapter) + Resend magic-link       │
│   - Dashboard API, replay endpoints, exports                │
│   - SSE fan-out subscribed to Redis pub/sub (maxDuration 5m)│
│   - Issues researcher commands to engine via Redis stream   │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
       ┌───────▼─────────────┐        ┌──────▼──────────┐
       │  Railway Postgres   │        │  Railway Redis  │
       │  (managed,          │        │  (command       │
       │   pgBouncer,        │◄───────┤   streams +     │
       │   nightly backups)  │ writes │   event pub/sub)│
       └───────▲─────────────┘        └──────▲──────────┘
               │                              │
               │ writes state, decisions      │ commands + events
               │                              │
┌──────────────┴──────────────────────────────┴──────────────┐
│       Verbio Engine (Python 3.12, Railway Dockerfile)      │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  LiveKit Agent: joins room as moderator participant │  │
│   │  - Subscribes to per-participant audio tracks       │  │
│   │  - Streams audio to Deepgram (per track)            │  │
│   │  - Maintains per-participant ParticipantState       │  │
│   │  - Runs Tick Loop (2 Hz): RulesEngine.evaluate()    │  │
│   │  - On non-silent decision: LLM → TTS → publish      │  │
│   │  - Persists via SQLAlchemy async; events to Redis   │  │
│   └─────────────────────────────────────────────────────┘  │
└──────────────┬──────────────────────────────┬──────────────┘
               │                              │
       ┌───────▼────────┐         ┌───────────▼──────────┐
       │   LiveKit      │         │  Cloudflare R2       │
       │   Cloud/SFU    │         │  (S3-compatible      │
       │ ← participants │         │   recording storage) │
       └────────────────┘         └──────────────────────┘
```

---

## 3. Service boundaries

| Service                                                                            | Owns                                                                                                                                                             | Does not own                                                                   |
| ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| **`verbio-web`** (Next.js, TypeScript, Vercel)                                     | Dashboard, auth (Auth.js v5 + Resend), session lifecycle endpoints, replay UI/API, exports, study config UI, SSE fan-out from Redis                              | Real-time decision logic. State derivation. Direct LiveKit room participation. |
| **`verbio-engine`** (Python 3.12, LiveKit Agents SDK, FastAPI, Railway/Dockerfile) | Tick loop, `SessionState`, rules, decision resolution, LLM/TTS orchestration, LiveKit room participation, transcript ingestion, persistence via SQLAlchemy async | User identity & sessions, multi-org concerns, replay UI, exports.              |
| **`verbio-shared`** (TypeScript + Python via codegen)                              | Canonical wire shapes: `ParticipantState`, `ModeratorDecision`, `RuleEvaluation`, `ResearcherCommand`. **Pydantic is the source of truth.**                      | Runtime logic.                                                                 |

### Engine process model

**One engine process per active session.** Spawned by a thin supervisor on Railway (same project as Postgres + Redis, so engine ↔ db ↔ redis traffic stays on the Railway private network — the web side reaches data over public TLS). Spawning per-session simplifies isolation (one session's tick latency can't starve another), failure handling (crash → supervisor relaunches with state replay from last persisted tick), and observability (one process = one trace tree).

The supervisor is the only shared engine component. It watches the `sessions` table for `status='live'` transitions and ensures exactly one agent process is running per live session.

### Why two languages

- **Python for the engine:** LiveKit Agents SDK is Python-first; Silero VAD, numpy for state math, and the rules engine read more cleanly in Python.
- **TypeScript for the web:** Next.js (App Router), Auth.js v5, Prisma client (read-mostly), React, `EventSource` for live updates. Anything else would be a regression.

The shared-types pipeline (Pydantic → JSON Schema → TS) is the seam that keeps the two services from drifting.

---

## 4. Data flow — a single tick

The engine runs at 2 Hz (configurable, 500 ms interval). Each tick is a pure function of state → decision, plus side effects.

```text
on_tick(t):
  1. state = state_store.advance_to(t)              # update from event buffers
  2. commands = command_bus.drain(session_id)       # researcher inputs
  3. evaluations = rules_engine.evaluate_all(state, t)  # deterministic

  4. if commands:
       decision = commands_to_decision(commands, state)
     else:
       decision = resolve_decision(evaluations,
                                   quietness_budget,
                                   cooldowns)

  5. persist_tick(state_snapshot=state,             # PERSIST FIRST
                  evaluations=evaluations,
                  decision=decision)

  6. if decision.action != "stay_silent" and not moderator_muted:
       llm_output = mouth.generate(decision, state, persona)
       audio = tts.synthesize(llm_output, persona.voice_id)
       livekit.publish_audio(audio)
       mark_executed(decision, llm_output, audio_url, spoken_at)
       update_cooldowns_and_budget(decision)

  7. realtime.publish(session_id, "tick", decision_summary)
```

### Two absolute invariants

1. **Persistence before execution.** A crash mid-tick must never leave a spoken utterance with no decision record. Write the `decisions` row + `rule_evaluations` rows _before_ invoking mouth → TTS → publish.
2. **The tick loop never blocks on LLM / TTS.** If they exceed the latency budget, the decision is logged as `was_executed=false` with `suppressed_by=["latency_exceeded"]` and the next tick proceeds. Slow inference cannot stall the loop.

### Why `stay_silent` is persisted

The dashboard's **Why quiet now?** panel — the trust feature — depends on the decision log containing equal-fidelity records for silent ticks. Without them, researchers cannot tell whether the engine is reasoning continuously or has stalled.

---

## 5. Latency budget

End-of-rule-trigger → first audible word:

| Metric | Target    | Enforcement                       |
| ------ | --------- | --------------------------------- |
| p95    | ≤ 1500 ms | Synthetic CI perf test (Phase 4+) |
| p99    | ≤ 2500 ms | Same                              |

If we miss this budget, the moderator interrupts the next speaker instead of filling silence — which destroys the "bias toward silence" property in practice. The mouth layer streams tokens to TTS as they arrive to hit it. Pre-synthesized fallback phrasings (per persona) cover the LLM-failure path with near-zero added latency.

---

## 6. Storage layout (Railway Postgres)

See brief §10.1 for the canonical schema. Key tables:

| Table                | Purpose                                                  |
| -------------------- | -------------------------------------------------------- |
| `studies`            | Reusable session configurations + persona + rules config |
| `sessions`           | Live / ended session instances + frozen config snapshot  |
| `participants`       | Identities present in each session                       |
| `utterances`         | Transcribed audio segments per participant               |
| `state_snapshots`    | Full `SessionState` per tick (2 Hz → 7200 rows/hour)     |
| `decisions`          | Every tick's decision, **including `stay_silent`**       |
| `rule_evaluations`   | Every rule's evaluation, **fired or not**                |
| `researcher_actions` | Inbound `ResearcherCommand` log                          |
| `session_flags`      | Researcher-flagged moments + auto-generated flags        |

**Tenant isolation via the application-layer `scopedDb(orgId)` helper** — no DB-side RLS. See brief §10.3 and ADR-0002 for the tradeoff rationale. Every web read goes through the helper, which injects `where: { orgId }` and rejects writes that would mutate `org_id`. Direct `prisma.<model>` access is lint-banned. The engine validates inbound command `org_id` against the session's `org_id` before applying. No admin escape hatches — admins use elevated org roles on the same auth path.

State-snapshot retention: full 2 Hz fidelity for 30 days, then downsampled to 1 Hz. Storage cost is trivial vs. analysis value.

---

## 7. Communication transports

| Direction                                       | Transport                                                                                   | Why                                                                                                                                                                                                                           |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Browser ↔ Web (auth, REST)                      | HTTPS                                                                                       | Standard                                                                                                                                                                                                                      |
| Browser ↔ Web (live tile updates, decision log) | SSE (`EventSource`) → Next.js route → Redis pub/sub                                         | Read-only fan-out; native browser auto-reconnect; no custom WS server. Vercel function `maxDuration: 300` forces a clean reconnect every 5 min, which the route handles transparently via `last-event-id` + Postgres backfill |
| Web (Vercel) → Postgres (Railway)               | TLS over public internet, via `DATABASE_URL_POOLED` (pgBouncer transaction mode, port 6543) | Required because Vercel serverless can't join Railway's private network; pgBouncer absorbs the per-invocation connection churn                                                                                                |
| Web (Vercel) → Redis (Railway)                  | TLS via `rediss://`                                                                         | Same cross-vendor constraint; used by the SSE route's pub/sub subscription and the researcher-command stream producer                                                                                                         |
| Web → Engine (researcher commands)              | Redis stream `verbio:commands:{session_id}`                                                 | Durable, ordered, supports consumer groups; engine drains per tick                                                                                                                                                            |
| Engine → Web (decision events)                  | Redis pub/sub channel `verbio:events:{session_id}`                                          | Low-latency fan-out; SSE bridge backfills missed rows from Postgres on reconnect                                                                                                                                              |
| Engine ↔ LiveKit                                | LiveKit SDK (WebRTC)                                                                        | Required for room participation                                                                                                                                                                                               |
| Engine ↔ Deepgram                               | WebSocket (Deepgram streaming)                                                              | Lowest-latency STT                                                                                                                                                                                                            |
| Engine ↔ DeepSeek (api.deepseek.com)            | HTTPS (streaming, OpenAI-compatible)                                                        | Token streaming feeds TTS                                                                                                                                                                                                     |
| Engine ↔ Cartesia/ElevenLabs                    | HTTPS (streaming PCM)                                                                       | Audio published as LiveKit track                                                                                                                                                                                              |

---

## 8. Repository layout

See brief §12. The monorepo is a hybrid of pnpm workspaces (JS) and a separately-managed Python project (engine). The two never share runtime code, only generated types.

```
verbio/
├── apps/web/                  # Next.js dashboard
├── services/engine/           # Python engine
├── packages/
│   ├── shared-types/          # Generated TS types
│   ├── ui/                    # Shared React components
│   └── eslint-config/         # Shared lint config
├── schemas/                   # Pydantic source of truth → JSON Schema → TS
├── infra/
│   ├── postgres/migrations/   # Alembic migrations (source of truth)
│   ├── railway/               # railway.toml + per-service definitions
│   ├── r2/                    # Cloudflare R2 lifecycle policies
│   └── docker-compose.dev.yml # Local Postgres + Redis
├── docs/                      # This directory
└── verbio-engineering-brief.md
```

---

## 9. Quality gates (summary)

| Area         | Gate                                                                                                                                                |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| TypeScript   | strict mode, no `any` without inline justification, `recommended-type-checked`                                                                      |
| Python       | `ruff check` + `ruff format --check` + `mypy --strict`                                                                                              |
| Boundaries   | Pydantic at every external boundary (HTTP, Redis, env); no `dict`-typed payloads across modules                                                     |
| Tests        | ≥ 85% line coverage on `verbio_engine/rules/` and `tick_loop.py`; property tests on state math                                                      |
| Shared types | Generated TS must equal regenerated TS — CI fails otherwise                                                                                         |
| Migrations   | All Postgres schema via Alembic (`infra/postgres/migrations/`), forward-only; web Prisma client regenerated via `prisma db pull`; CI fails on drift |
| Commits      | Conventional Commits, enforced by commitlint                                                                                                        |
| Secrets      | env vars only, validated at boot                                                                                                                    |

---

## 10. Open architectural questions

- Topic clustering for `stalled_thread` rule — may defer to phase 5 / 6.
- Multi-region engine deployment for latency-sensitive clients.
- Whether `state_snapshots` belongs in cold storage after 30 days instead of downsampling in place.
- Whether the supervisor should be a separate service or an internal admin module on the engine.

Decisions made on these will land as ADRs under `docs/adr/`.
