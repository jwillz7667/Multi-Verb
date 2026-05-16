# Railway — verbio-engine, Postgres, Redis

Railway hosts the three services that need a long-lived process or a
private network:

| Service           | Type                    | Role                                                                                     |
| ----------------- | ----------------------- | ---------------------------------------------------------------------------------------- |
| `verbio-engine`   | Docker (this repo)      | FastAPI + LiveKit Agents process — 2 Hz tick loop, rules engine, mouth/TTS orchestration |
| `verbio-postgres` | Railway Postgres plugin | Single source of truth for sessions, decisions, rule_evaluations, audit trail            |
| `verbio-redis`    | Railway Redis plugin    | SSE pub/sub channel + researcher command bus + ephemeral state                           |

The web tier lives on Vercel, not Railway — see ADR-0002 for the
rationale (Next.js performance + edge runtime, vs. Railway's
long-running-process strengths).

## Layout

```
infra/railway/
├── README.md          # this file
└── railway.toml       # verbio-engine service config (committed)
```

Postgres and Redis are provisioned via Railway's UI ("Add Plugin");
there's no `.toml` for plugin services. Their connection strings
flow into `verbio-engine` and `verbio-web` via Railway service
references (e.g. `${{ Postgres.DATABASE_URL }}`).

## Cross-vendor connectivity

Vercel functions can't share Railway's private network, so all
web → Postgres / Redis traffic crosses the public internet. We
mitigate the connection-storm risk:

- **Postgres**: Vercel uses `DATABASE_URL_POOLED` (pgBouncer
  transaction mode on port 6543). Engine uses `DATABASE_URL_ENGINE`
  (direct on port 5432) because it holds one persistent connection
  per session worker.
- **Redis**: TLS-required (`rediss://`). Engine and web both hold
  small persistent pools — no per-request connect/disconnect.
- **Region**: Pin both Railway and Vercel to `us-east-1` so the
  RTT stays under 5ms on the cross-vendor leg.

## Deploy flow

1. Push to `main` triggers Railway's GitHub integration.
2. Railway builds from `services/engine/Dockerfile` (build context is
   the repo root so `COPY schemas/` works for schema parity tests).
3. Health check on `/health` must return 200 before the new
   revision takes traffic.
4. Old revision drains in-flight LiveKit sessions before terminating
   (LiveKit Agents handles this; the FastAPI surface is independent).

## Local parity

Locally, Postgres + Redis run from `infra/docker-compose.dev.yml`.
The engine connects via the same env-var names as in Railway, so
swapping environments is just a `.env.local` change.

## Phase 0 done-when

- Railway project exists with `verbio-engine` (Docker), `Postgres`,
  `Redis` services.
- `railway.toml` checked in (this commit).
- GitHub integration deploys engine on push to `main`.
- `/health` returns 200 in the deployed environment.
- Engine env vars set in Railway dashboard per `.env.example` keys.
