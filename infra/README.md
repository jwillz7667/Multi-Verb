# infra/

Infrastructure-as-code lives here. Each subdirectory owns one
externally-managed thing — Postgres schema, Railway services, R2
bucket policies — plus the docker-compose stack that mirrors
production locally.

## Layout

```
infra/
├── README.md                    # this file
├── docker-compose.dev.yml       # local Postgres + pgBouncer + Redis (+ pgAdmin debug profile)
├── postgres/
│   ├── README.md
│   └── migrations/              # Alembic — single source of truth for the schema
└── r2/
    ├── README.md
    └── lifecycle.json           # Cloudflare R2 bucket lifecycle policy
└── railway/
    ├── README.md
    └── railway.toml             # verbio-engine service config
```

## What's NOT here

- **Vercel config.** `apps/web/vercel.json` (when needed) belongs
  next to the app it configures. Vercel project settings (env vars,
  domains, integrations) are managed through the Vercel dashboard,
  not committed.
- **Auth.js / Resend config.** Both are runtime concerns living in
  `apps/web/src/auth.ts` (lands in the Phase 0 Auth.js commit, not
  this scaffold).
- **LiveKit Cloud.** Configured per-room at runtime; no infra IaC.

## Local development substrate

```bash
docker compose -f infra/docker-compose.dev.yml up -d
```

This brings up:
- **Postgres 16** on `localhost:5432` (direct) — for Alembic and engine async pool.
- **pgBouncer** on `localhost:6543` (transaction mode) — what Vercel functions hit.
- **Redis 7** on `localhost:6379` — pub/sub + command bus.
- **pgAdmin** on `localhost:5050` — optional debug UI; requires `--profile debug`.

Credentials are `verbio` / `verbio` — local only. Production
credentials live in Railway service variables.

## Production substrate

| Host | What | Why |
| --- | --- | --- |
| **Vercel** (`iad1`) | `apps/web` | Edge-deployed Next.js, RSC, ISR, serverless API routes |
| **Railway** (`us-east1`) | `verbio-engine` + Postgres + Redis | One-process-per-session engine needs durable runtime + private network for DB |
| **Cloudflare R2** | recordings + exports | S3-compatible, zero egress fees |
| **LiveKit Cloud** | SFU | Audio rooms; engine joins as participant |
| **Resend** | transactional email | Auth.js magic links + study invites |
| **Deepgram** | STT (Phase 2+) | Nova-3 for transcription |
| **Anthropic** | mouth LLM (Phase 4+) | Claude Haiku for phrasing decisions |
| **Cartesia** + ElevenLabs | TTS (Phase 4+) | Sonic primary, Flash fallback |

The vendor split is documented in `docs/adr/0002-stack.md`.
