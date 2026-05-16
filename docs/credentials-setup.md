# Credentials setup — where every env var comes from

This guide tells you exactly **where to sign up**, **which dashboard page holds the value**, and **how to set it** on Vercel + Railway. Pair with [`.env.example`](../.env.example) for the canonical list of variable names; this file is the human-side walk-through.

Conventions used in the commands below:

```bash
# Vercel — apps/web is linked to project js-projects-995e4cf4/multi-verb-web
cd apps/web
printf '<value>' | vercel env add VAR_NAME production
printf '<value>' | vercel env add VAR_NAME preview
printf '<value>' | vercel env add VAR_NAME development
# Sensitive vars: append --sensitive to production/preview only (Vercel disallows it on development)

# Railway — repo root is linked to project thorough-freedom (id 6d186fa7-bc87-41f3-99a3-7ad8b4a805b5)
railway service <ServiceName>           # pick which service the var attaches to
railway variables --set "VAR_NAME=value"
```

After any Vercel change, re-pull locally:

```bash
cd apps/web && vercel env pull .env.local --yes
```

---

## 1. LiveKit Cloud — `LIVEKIT_*` + `NEXT_PUBLIC_LIVEKIT_URL`

**Why:** SFU + media server. Every browser participant connects here; the engine joins the same room as the moderator. Required from Phase 1 onward.

1. Sign up at <https://cloud.livekit.io>. Free tier covers 100 concurrent participants and 50 GB egress / month — enough for development plus one live study.
2. Create a project — call it `verbio-dev` (and a second `verbio-prod` later for production isolation).
3. Open the project, then **Settings → Keys** in the left nav. The "Project URL", "API Key", and "API Secret" are all on this page.

Set them:

```bash
printf 'wss://verbio-dev-XXXXXXXX.livekit.cloud' | vercel env add LIVEKIT_URL production --sensitive
printf 'wss://verbio-dev-XXXXXXXX.livekit.cloud' | vercel env add NEXT_PUBLIC_LIVEKIT_URL production
printf 'APIxxxxxxxxxxxx'                          | vercel env add LIVEKIT_API_KEY production --sensitive
printf 'secret-from-dashboard'                    | vercel env add LIVEKIT_API_SECRET production --sensitive
# repeat for preview + development (drop --sensitive on development)
```

`NEXT_PUBLIC_LIVEKIT_URL` is intentionally exposed to the browser (Next.js inlines it); `LIVEKIT_API_KEY/SECRET` stay server-side.

---

## 2. Resend — `AUTH_RESEND_KEY` + `AUTH_EMAIL_FROM`

**Why:** Magic-link delivery for Auth.js v5. Required for any researcher to sign in.

1. Sign up at <https://resend.com>. Free tier = 3,000 emails / month + 100 / day.
2. **Add a sending domain**: Domains → Add Domain → `verbio.app` (or whatever you own). Resend gives you 3 DNS records (SPF, DKIM, return-path); add them at your registrar. Verification takes 1–60 min.
3. **Create an API key**: API Keys → Create API Key. Scope = "Sending access" only. Copy the `re_xxxxxxxxxxxx` value once; it's not shown again.

Set them:

```bash
printf 're_xxxxxxxxxxxxxxxxxxxxxxxx'             | vercel env add AUTH_RESEND_KEY production --sensitive
printf 'Verbio <auth@verbio.app>'                 | vercel env add AUTH_EMAIL_FROM production
```

Until your domain is verified, you can test with the Resend sandbox `onboarding@resend.dev` sender — but the magic-link emails will only deliver to the email address that owns the Resend account.

---

## 3. Deepgram — `DEEPGRAM_API_KEY`

**Why:** Streaming STT (Nova-3 model). Engine subscribes to each participant's audio track and feeds it to Deepgram for real-time transcripts.

1. Sign up at <https://console.deepgram.com>. $200 free credit at signup — enough for ~3,000 minutes of Nova-3 streaming.
2. Create a project → **API Keys → Create a New API Key**. Scope = "Member" or "Admin" (Member is fine for ingestion).
3. Copy the key. Deepgram shows it only once.

This var belongs on the **engine** (Railway), not the web app. Once the engine service is deployed:

```bash
railway service verbio-engine
railway variables --set "DEEPGRAM_API_KEY=<your-key>"
railway variables --set "DEEPGRAM_MODEL=nova-3-general"
railway variables --set "DEEPGRAM_LANGUAGE=en-US"
```

For local engine development, add it to `services/engine/.env` (gitignored):

```
DEEPGRAM_API_KEY=<your-key>
```

---

## 4. DeepSeek — `DEEPSEEK_API_KEY` (Phase 4+)

**Why:** The mouth layer. Phrases the engine's `ModeratorDecision` into a one-sentence prompt. **Default model = `deepseek-chat`** (V3 family) for general use; per-study upgrade to `deepseek-reasoner` (R1) when reasoning quality matters more than latency.

DeepSeek exposes an OpenAI-compatible API — clients use the standard `openai` SDK with `base_url="https://api.deepseek.com"`.

1. Sign up at <https://platform.deepseek.com>. Add a payment method — `deepseek-chat` is ~$0.27/M input tokens (cache miss) / $0.07/M cache hit, $1.10/M output. A 60-min session ≈ 50 phrasings × 120 tokens ≈ **$0.002** (cheaper than Anthropic Haiku by ~15×).
2. **API Keys → Create new API key**. Scope = default (full).
3. Copy the `sk-xxxxxxxxxxxx` value.

Engine-side env var, deploy-time:

```bash
railway service verbio-engine
railway variables --set "DEEPSEEK_API_KEY=<your-key>"
railway variables --set "DEEPSEEK_BASE_URL=https://api.deepseek.com"
railway variables --set "DEEPSEEK_MODEL_DEFAULT=deepseek-chat"
railway variables --set "DEEPSEEK_MODEL_UPGRADE=deepseek-reasoner"
railway variables --set "DEEPSEEK_TIMEOUT_MS=1200"
```

**Latency caveat:** DeepSeek's first-token latency is generally higher and more variable than Anthropic Haiku — expect more frequent triggering of the §8.4 templated fallback path. That is acceptable (fallback is real product, not a panic exit), but worth tracking with the `llm_fallback=true` metric during pilot sessions.

---

## 5. Cartesia — `CARTESIA_API_KEY` (Phase 4+)

**Why:** Primary TTS. Streams audio tokens as the LLM produces text, so the moderator can speak with sub-second latency.

1. Sign up at <https://play.cartesia.ai>. Free tier = 10k characters / month.
2. **Dashboard → API Keys → New API Key**.
3. Copy.

Engine-side:

```bash
railway service verbio-engine
railway variables --set "CARTESIA_API_KEY=<your-key>"
railway variables --set "CARTESIA_MODEL=sonic-english"
```

### 5b. ElevenLabs fallback — `ELEVENLABS_API_KEY`

Optional but recommended — engine falls over to ElevenLabs Flash if Cartesia times out.

1. Sign up at <https://elevenlabs.io>. Free tier = 10k characters / month.
2. **Profile → API Keys → Create**.
3. Copy.

```bash
railway variables --set "ELEVENLABS_API_KEY=<your-key>"
railway variables --set "ELEVENLABS_MODEL=eleven_flash_v2_5"
```

---

## 6. Cloudflare R2 — `R2_*` (Phase 6+)

**Why:** S3-compatible recording + export storage. Lower egress costs than S3 since Cloudflare doesn't charge for data leaving R2.

1. Sign up at <https://dash.cloudflare.com> (free; R2 itself is pay-as-you-go: $0.015/GB/month storage, free egress).
2. **R2 Object Storage → Overview**: note the **Account ID** (top right or in the API tokens section).
3. **R2 → Manage R2 API Tokens → Create API token**:
   - Permissions: "Object Read & Write"
   - Specify bucket: `verbio-recordings` (create the bucket first via **R2 → Create Bucket**, region "Automatic")
   - Copy the **Access Key ID** and **Secret Access Key** — shown once.
4. Create a second bucket for exports: `verbio-exports`.
5. Optional: set up a custom domain for public CDN URLs at **R2 → Bucket → Settings → Public Access**.

Set on Vercel (web issues the signed URLs):

```bash
printf '<account-id>'      | vercel env add R2_ACCOUNT_ID production --sensitive
printf '<access-key-id>'   | vercel env add R2_ACCESS_KEY_ID production --sensitive
printf '<secret-key>'      | vercel env add R2_SECRET_ACCESS_KEY production --sensitive
printf 'verbio-recordings' | vercel env add R2_BUCKET production
# optional CDN public origin (else fall back to s3 endpoint)
printf 'https://cdn.verbio.app' | vercel env add R2_PUBLIC_BASE_URL production
```

Set on Railway too (engine uploads recordings directly):

```bash
railway service verbio-engine
railway variables --set "R2_ACCOUNT_ID=<id>" \
                  --set "R2_ACCESS_KEY_ID=<id>" \
                  --set "R2_SECRET_ACCESS_KEY=<secret>" \
                  --set "R2_BUCKET_RECORDINGS=verbio-recordings" \
                  --set "R2_BUCKET_EXPORTS=verbio-exports" \
                  --set "R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com"
```

---

## 7. Sentry — `SENTRY_DSN_WEB`, `SENTRY_DSN_ENGINE` (Phase 0+, optional until Phase 7)

**Why:** Exception tracking + performance traces. Two separate projects so web vs. engine errors don't tangle.

1. Sign up at <https://sentry.io>. Free tier = 5k errors + 10k traces / month.
2. **Projects → Create Project**:
   - First project: platform "Next.js", name `verbio-web`. Copy the DSN.
   - Second project: platform "Python", name `verbio-engine`. Copy the DSN.

Web:

```bash
printf 'https://xxx@oXXX.ingest.sentry.io/XXX' | vercel env add SENTRY_DSN_WEB production --sensitive
printf 'production' | vercel env add SENTRY_ENVIRONMENT production
```

Engine:

```bash
railway service verbio-engine
railway variables --set "SENTRY_DSN_ENGINE=https://xxx@...ingest.sentry.io/XXX" \
                  --set "SENTRY_ENVIRONMENT=production" \
                  --set "SENTRY_TRACES_SAMPLE_RATE=0.1"
```

---

## 8. Pre-set / no action needed

These were already set during infra provisioning ([`scripts/`](../) commands run earlier):

| Variable               | Source                                          | Where set                                                    |
| ---------------------- | ----------------------------------------------- | ------------------------------------------------------------ |
| `DATABASE_URL_POOLED`  | Railway Postgres (public proxy, port 31749)     | Vercel × all 3 envs                                          |
| `DATABASE_URL_DIRECT`  | Same                                            | Vercel × all 3 envs                                          |
| `REDIS_URL`            | Railway Redis (public proxy, port 15792)        | Vercel × all 3 envs                                          |
| `REDIS_NAMESPACE`      | `verbio:prod` / `verbio:preview` / `verbio:dev` | Vercel, per env                                              |
| `AUTH_SECRET`          | `openssl rand -base64 32` (auto-generated)      | Vercel × all 3 envs                                          |
| `AUTH_TRUST_HOST`      | `true`                                          | Vercel × all 3 envs                                          |
| `AUTH_EMAIL_FROM`      | Placeholder `Verbio <noreply@verbio.app>`       | Vercel × all 3 envs — **change once Resend domain verified** |
| `NEXT_PUBLIC_APP_NAME` | `Verbio`                                        | Vercel × all 3 envs                                          |
| `NEXT_PUBLIC_APP_URL`  | Per-env Vercel canonical                        | Vercel × all 3 envs                                          |
| `ENGINE_ADMIN_TOKEN`   | `openssl rand -base64 32` (auto-generated)      | Vercel × all 3 envs                                          |

### Items still pending engine deployment

| Variable                                | Notes                                                                                                                                                                                                                |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ENGINE_BASE_URL` (Vercel)              | Set to the engine's Railway public URL once the engine service is deployed in Phase 7. Format: `https://<service>-production.up.railway.app`.                                                                        |
| Engine-side `DATABASE_URL`, `REDIS_URL` | When the engine service is created, reference Railway's built-in variables: `${{Postgres.DATABASE_URL}}` and `${{Redis.REDIS_URL}}` (these use the internal `.railway.internal` hosts — much faster + no proxy hop). |

---

## How to verify everything's wired

Once all the above are set, from `apps/web`:

```bash
pnpm exec prisma db push --skip-generate          # connectivity smoke against Postgres
pnpm exec tsx -e "import {createClient} from 'redis'; const c = createClient({url: process.env.REDIS_URL!}); await c.connect(); console.log(await c.ping()); await c.quit();"
```

From the repo root:

```bash
DATABASE_URL_DIRECT="$(grep DATABASE_URL_DIRECT apps/web/.env.local | cut -d= -f2- | tr -d '"')" \
  uv --project services/engine run alembic current
```

All three should succeed without error and the third should print `0003_phase1_persistence (head)`.
