# ADR 0002: Stack — Vercel for web, Railway for engine + data, drop Supabase

- **Status:** Accepted
- **Date:** 2026-05-16
- **Deciders:** Engineering team
- **Related:** ADR-0001, brief §4 (System architecture), §10 (Data layer), §11.3 (Realtime transport), §13 (Quality gates), §14 (Delivery phases)

## Context

The original brief proposed a Supabase + Vercel topology: Supabase Postgres with Realtime + Storage + Auth + RLS for the data layer, Vercel for the Next.js web app, Railway for the Python engine, and Redis as a side service. That stack is reasonable, but during pre-Phase-0 scoping we re-evaluated the data layer and the hosting split and arrived at two coupled decisions:

1. **Drop Supabase entirely.** Replace it with primitives we control end-to-end: Railway-managed Postgres + Redis, Cloudflare R2 for blob storage, and Auth.js v5 + Resend for authentication. Tenant isolation moves from RLS to an application-layer `scopedDb(orgId)` helper.
2. **Specialize hosting per workload.** Vercel hosts the Next.js web app; Railway hosts the Python engine, the managed Postgres, and the managed Redis. Each vendor is best-in-class for its half of the workload, and the cross-vendor traffic between them is bounded and pooled.

Both changes happen _before_ any code lands, so the cost is purely documentation churn — the right time to make them.

### Forces

- **Vendor specialization beats consolidation for this workload.** Vercel is purpose-built for Next.js: per-PR preview environments, edge network, RSC streaming, ISR, and image optimization are all first-class and require zero config. Railway is purpose-built for long-lived Python services and managed Postgres + Redis: the engine needs a real process supervisor, a stateful tick loop, and a private network to the database. Forcing either workload onto the other vendor's runtime sacrifices a meaningful chunk of value. The cost we pay for splitting — cross-vendor traffic between Vercel and Railway — is bounded (web reads via pooled Postgres connections and Redis pub/sub) and mitigated by region co-location (Vercel `iad1` + Railway `us-east1`).
- **Cost predictability.** Supabase's per-feature pricing (Realtime, Storage, Auth, Edge Functions) compounds. Vercel's plan-based pricing for the web and Railway's project-based pricing for the engine + data plus R2's egress-free model is easier to forecast for a low-throughput, high-retention research workload.
- **Decoupling auth from the database vendor.** Supabase Auth ties identity to the database project; migrating Postgres without migrating auth is painful. Auth.js + Resend lets us choose Postgres independently and gives us first-class control over the schema (`users`, `accounts`, `sessions`, `verification_tokens`).
- **RLS is overkill here.** Supabase RLS shines when untrusted clients (browser) talk to the DB directly. Our browser never touches Postgres — every read goes through Next.js, every write through the engine. RLS would be defense-in-depth, but the real isolation already lives in the server code. Removing RLS removes a class of "policy works in test, breaks under JWT-changes" bugs.
- **Realtime is solvable with SSE + Redis.** Supabase Realtime is convenient but is an extra moving part (logical replication slot, channel multiplexer, JWT-scoped row filtering). For our fan-out pattern (engine → all researchers on a single session), a per-session Redis pub/sub channel bridged through a Next.js SSE route is simpler, cheaper, and gives us native browser auto-reconnect via `EventSource`. The Vercel serverless function `maxDuration: 300` ceiling is absorbed by the same `last-event-id` reconnect + Postgres backfill we needed anyway for network blips.

## Decision

Adopt the following stack for all environments (dev, preview, staging, production):

| Capability                 | Choice                                                                                                                       | Notes                                                                                                                                                                                                                                                          |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Web compute                | **Vercel**                                                                                                                   | Next.js 15 (App Router); GitHub integration drives preview deploys per PR; production region `iad1`                                                                                                                                                            |
| Engine compute             | **Railway (Dockerfile)**                                                                                                     | Python 3.12; one process per active session via thin supervisor; region `us-east1` to co-locate with Vercel `iad1`                                                                                                                                             |
| Relational DB              | **Railway Postgres (managed)**                                                                                               | pgBouncer, nightly backups; one logical database, two roles (`postgres` migrations, `verbio_engine` runtime grants); web reaches it via `DATABASE_URL_POOLED` (port 6543, transaction mode) over public TLS; engine reaches it via the Railway private network |
| Cache + bus                | **Railway Redis (managed)**                                                                                                  | Command streams (`verbio:commands:{session_id}`) + event pub/sub (`verbio:events:{session_id}`); web reaches it via `rediss://` over public TLS; engine reaches it via the Railway private network                                                             |
| Schema migrations          | **Alembic** in `infra/postgres/migrations/`, driven from `services/engine`                                                   | Source of truth; CI applies via `alembic upgrade head`                                                                                                                                                                                                         |
| Engine ORM                 | **SQLAlchemy 2.0 async + asyncpg + Pydantic**                                                                                | Strict typed models at every boundary                                                                                                                                                                                                                          |
| Web ORM                    | **Prisma**, introspected via `prisma db pull` against the Alembic schema                                                     | Web never authors schema; CI fails on drift                                                                                                                                                                                                                    |
| Auth                       | **Auth.js v5** with the Postgres adapter                                                                                     | HS256 sessions signed with `AUTH_SECRET`, 15-min access / 30-day refresh; rotated quarterly                                                                                                                                                                    |
| Transactional email        | **Resend**                                                                                                                   | Magic-link delivery; verified sender on the production domain                                                                                                                                                                                                  |
| Recording + export storage | **Cloudflare R2**                                                                                                            | AES-256 at rest; signed URLs (15-min recordings, 60-min exports); lifecycle policies in `infra/r2/`                                                                                                                                                            |
| Browser realtime           | **SSE** from a Next.js route subscribed to Redis pub/sub                                                                     | Route declares `maxDuration: 300` (Vercel Pro ceiling); `EventSource` reconnects with `last-event-id`; SSE route backfills missed rows from Postgres before resuming the live tail                                                                             |
| Tenant isolation           | **Application-layer `scopedDb(orgId)` helper** + lint rule banning direct Prisma access + engine command `org_id` validation | See §10.3 of the brief; replaces Supabase RLS                                                                                                                                                                                                                  |

LiveKit Cloud, Deepgram, Anthropic, Cartesia, and ElevenLabs are unchanged.

## Alternatives considered

- **Keep the original Supabase + Vercel stack.** Rejected. We would inherit Supabase Realtime, Supabase Auth, and Supabase Storage — three convenient features that we can replace individually with simpler primitives, and that we'd otherwise have to migrate off later if we ever wanted to leave Supabase. Doing it now while the codebase has zero lines is free; doing it after Phase 4 is a quarter of work.

- **Consolidate web on Railway via Nixpacks (one-vendor stack).** Considered. Putting Next.js on Railway alongside the engine would collapse to one dashboard, one billing relationship, and a private network for web↔db traffic. Rejected because we lose Vercel's per-PR preview environments, edge network, ISR, RSC streaming, and image optimization — features that materially improve researcher-facing web DX and that we would otherwise rebuild ourselves. The cross-vendor traffic cost is mitigated by `DATABASE_URL_POOLED` (pgBouncer transaction mode on port 6543) absorbing serverless connection churn, and by co-locating Vercel `iad1` with Railway `us-east1`.

- **Postgres on Neon (serverless) instead of Railway Postgres.** Rejected for now. Neon's branching is attractive for PR previews, but Railway already gives us per-PR Postgres via preview environments, and keeping Postgres in the same Railway project as the engine puts engine↔db traffic on a private network. Revisit if we need branch-per-PR semantics for schema experimentation.

- **Drizzle ORM (web) instead of Prisma.** Considered. Drizzle's TS-first schema would let us declare the schema directly in TS without `db pull`. Rejected because Alembic is authoritative (driven from the engine where the canonical Pydantic models live), so the web ORM is read-mostly and Prisma's introspection + generated client is the lowest-friction fit. We accept Prisma's generated-client weight as the cost of having a single source of truth.

- **Atlas (schema-as-code) instead of Alembic.** Considered. Atlas's HCL is nicer than Alembic's autogenerate dance, but Alembic is the de facto Python migration tool, lives in the engine repo with the rest of the engine's stack, and integrates cleanly with SQLAlchemy 2.0's metadata reflection. Atlas would add a second language for schema. Rejected on tooling-surface grounds.

- **better-auth (TS) instead of Auth.js v5.** Considered. better-auth is well-designed and ships with first-class TypeScript types, but Auth.js has wider production usage, an official Postgres adapter, and a mature email-provider integration. Rejected primarily on maturity; revisit in 12 months.

- **Native WebSocket from Next.js (custom protocol) instead of SSE.** Rejected. The browser channel is read-only fan-out — researcher commands take a separate HTTP POST → Redis stream path. WebSocket would force us to write reconnect logic, heartbeat logic, and message-id replay logic that `EventSource` provides natively. SSE is the right tool, and the `last-event-id` reconnect path doubles as the recovery story for Vercel's 5-minute function ceiling.

- **Keep Supabase Storage; only replace Auth + Realtime.** Rejected. Partial migrations leave us paying Supabase project fees and operational attention for a single feature, and Cloudflare R2 is materially cheaper for our access pattern (write-once, read-on-demand by researchers, occasional re-export).

## Consequences

**Easier:**

- Vercel's Next.js-native deploys, preview environments, and edge cache are free wins for researcher-facing web DX.
- Engine + data live in one Railway project on a private network — no cross-vendor hop for the hot path (engine ↔ Postgres ↔ Redis).
- Auth schema is ours — we can add SSO providers, audit logs, and org-scoped roles without negotiating with a vendor abstraction.
- Realtime is debuggable with `redis-cli` and `curl localhost:3000/api/sse/...`. No proprietary logical-replication channel to inspect.
- Schema source of truth lives next to the Pydantic models that already drive the shared-types pipeline.

**Harder:**

- Cross-vendor traffic: web ↔ Postgres and web ↔ Redis traverse the public internet (TLS). Mitigated by `DATABASE_URL_POOLED` (pgBouncer transaction mode on port 6543) absorbing serverless connection churn, by `rediss://` for Redis, and by region co-location (Vercel `iad1` + Railway `us-east1`). Watch p95 latency on web → db queries as a leading indicator.
- Vercel serverless functions have a 5-minute max duration (Pro). SSE routes declare `maxDuration: 300` and rely on `EventSource`'s native reconnect + `last-event-id` backfill from Postgres to span longer sessions. This is functionally invisible to the dashboard.
- Two vendor dashboards, two env-management surfaces, two billing relationships. Mitigated by mirroring env via `vercel env pull` for local dev and treating Railway env groups + Vercel project env as the two sources of truth, documented in `docs/runbook.md` §6.
- We carry the cost of building and maintaining the `scopedDb(orgId)` helper, the lint rule, and the cross-org integration tests. RLS would have given us defense-in-depth at the database layer for free; we are explicitly trading that for app-layer simplicity and accepting the test-coverage debt.
- We lose Supabase Realtime's row-level filtering and have to implement equivalent filtering in the SSE route (per-session subscription is the trivial case; richer filters would need more code).
- Auth.js requires us to wire and operate magic-link delivery via Resend — domain verification, bounce handling, deliverability monitoring — instead of letting Supabase handle it.

**New risks:**

- A bug in `scopedDb` is a cross-tenant data leak. Mitigation: dedicated unit + integration tests; lint enforcement; ESLint rule fails CI; quarterly tenant-scoping audit (added to `docs/runbook.md` §2 and `verbio-engineering-brief.md` §14 Phase 7).
- Postgres connection storms from cold-started Vercel functions. Mitigation: web _only_ uses `DATABASE_URL_POOLED` (pgBouncer transaction mode); set Prisma `connection_limit=1` per serverless invocation; alert on pgBouncer pool saturation.
- Railway and Vercel are both smaller than AWS. Mitigation: keep Postgres backups exported to R2 nightly (independent of Railway); Dockerfile-based engine deploy is portable to any container host; Next.js + Prisma + Auth.js are all open and portable to any Node host; Auth.js + Alembic are DB-vendor-neutral.

**Implied follow-up:**

- Phase 0 must provision the Vercel project (web), the Railway project (engine + Postgres + Redis), the R2 buckets, the Resend domain, and the Auth.js Postgres adapter. The brief's §14 Phase 0 has been updated to reflect this split.
- A future ADR will cover the `scopedDb` helper's design once it lands (Phase 1 or 2).
- A future ADR will cover backup + DR posture once Phase 7 hardening lands.

## References

- `verbio-engineering-brief.md` §4 (System architecture), §10.3 (Tenant scoping), §11.3 (Realtime transport), §14 Phase 0
- ADR-0001 (Record architecture decisions)
- [Vercel docs — Functions / maxDuration](https://vercel.com/docs/functions/configuring-functions/duration)
- [Railway docs — Postgres + Redis](https://docs.railway.com/)
- [Auth.js v5 — Postgres adapter](https://authjs.dev/getting-started/adapters/prisma)
- [Cloudflare R2 — S3 compatibility](https://developers.cloudflare.com/r2/api/s3/)
- [Alembic documentation](https://alembic.sqlalchemy.org/)
