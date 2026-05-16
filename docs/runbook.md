# Verbio — Operational Runbook

> Status: **skeleton**. This document is populated incrementally and reaches
> full coverage in Phase 7 (Hardening). On-call uses it as the primary
> reference for diagnosing and remediating live issues.

---

## 1. Service map

| Service | Where | Logs | Owner |
|---|---|---|---|
| `verbio-web` | Vercel | Vercel logs + Sentry | _TBD_ |
| `verbio-engine` | Railway (Dockerfile) | Railway logs + Sentry | _TBD_ |
| Postgres | Railway Postgres (managed addon) | Railway dashboard + `pg_stat_statements` | _TBD_ |
| Redis | Railway Redis (managed addon) | Railway dashboard | _TBD_ |
| Cloudflare R2 | Cloudflare account | Cloudflare dashboard | _TBD_ |
| LiveKit Cloud | LiveKit Cloud project | LiveKit dashboard | _TBD_ |
| Resend (transactional email) | Resend account | Resend dashboard | _TBD_ |

---

## 2. On-call basics

> Filled in Phase 7. Until then: page `@jwillz7667`.

### Escalation policy

1. _TBD_
2. _TBD_
3. _TBD_

### Communication

- Internal incident channel: _TBD_
- Status page: _TBD_

---

## 3. Common scenarios

### 3.1 Session is stuck (no decisions appearing in dashboard)

> _Populated during Phase 3._

Checklist:

- [ ] Confirm session `status='live'` in `sessions` table.
- [ ] Check engine process is alive (Railway logs, last tick timestamp).
- [ ] Inspect Redis: are commands accumulating in `verbio:commands:{session_id}` stream?
- [ ] Check the Next.js SSE route for the session — is it consuming `verbio:events:{session_id}` on Redis? `redis-cli pubsub numsub verbio:events:<id>` should be ≥ 1 per connected researcher.

### 3.2 Moderator did not speak when it should have

> _Populated during Phase 4._

Open the **Replay** view for the session and navigate to the tick in question. The `rule_evaluations` table answers "why didn't it?" — look for `suppressed_reason` values:

- `cooldown` — the rule fired but was within its cooldown window.
- `lower_priority_won` — another rule with higher priority fired the same tick.
- `quietness_budget` — global cap hit.
- `latency_exceeded` — LLM/TTS exceeded the budget; decision logged but not executed.
- `disabled` — rule disabled via feature flag in study config.

### 3.3 Moderator spoke when it should not have

> _Populated during Phase 4._

Open the decision row. Trace:

- `source` — was it `auto`, `researcher_manual`, or `researcher_whisper`?
- `triggering_rule` (if `auto`) — which rule fired?
- `reason_codes` — what predicate inputs triggered it?
- Inspect `llm_prompt` and `llm_output` — was the phrasing the issue?

### 3.4 LLM provider outage (Anthropic)

> _Populated during Phase 4._

Expected fallback behavior:

- Mouth layer's 800 ms wall-clock timeout triggers.
- Templated phrasing per `action` type is substituted from the per-persona cache.
- Decision row records `llm_fallback=true`.
- No intervention is ever silently skipped — fallback over nothing.

If templated audio cache is missing for a persona, log error and emit pre-synthesized generic-voice phrasing. Backfill cache offline.

### 3.5 TTS provider outage (Cartesia → ElevenLabs fallback)

> _Populated during Phase 4._

- Primary Cartesia call fails or exceeds budget.
- Engine switches to ElevenLabs Flash automatically.
- `decisions.tts_audio_url` reflects which provider served the clip (URL host).
- If both fail, the templated pre-synthesized audio is used (cached per persona).

### 3.6 LiveKit egress / recording failure

> _Populated during Phase 6._

- The session continues; live behavior is unaffected.
- Replay will be unavailable for the affected time window.
- Re-trigger egress for completed sessions via LiveKit API.

### 3.7 Engine crash mid-session

> _Populated during Phase 7 (chaos test landed)._

- Supervisor detects exit and relaunches the engine process.
- Engine loads last `state_snapshot` for the session and resumes ticking from `tick_id + 1`.
- All persisted decisions remain authoritative; nothing is replayed/re-executed.
- Researcher dashboard reconnects automatically via `EventSource` retry (using `last-event-id`); the SSE route backfills missed `decisions` rows from Postgres before resuming the live stream.

### 3.8 Dashboard offline (web outage)

- Sessions continue uninterrupted — the engine does not depend on `verbio-web`.
- Researcher commands cannot be issued during the outage.
- After recovery, the dashboard re-subscribes (`EventSource` reconnect with `last-event-id`) and the SSE route backfills missed `decisions` rows from Postgres before resuming live.

---

## 4. Diagnostic queries

> Skeleton. Real queries are added as scenarios are encountered.

```sql
-- Most recent tick per active session
SELECT s.id AS session_id, s.status, max(d.ts) AS last_decision_at
FROM sessions s
LEFT JOIN decisions d USING (id /* TODO actual FK */)
WHERE s.status = 'live'
GROUP BY s.id, s.status;

-- Decisions in a session, in chronological order
SELECT tick_id, action, target_participant_id, source, triggering_rule,
       was_executed, suppressed_by, llm_fallback
FROM decisions
WHERE session_id = $1
ORDER BY tick_id ASC;

-- Rules evaluated but never fired (why was it quiet?)
SELECT re.rule_name, count(*) AS evaluations,
       count(*) FILTER (WHERE fired) AS fires
FROM rule_evaluations re
JOIN decisions d ON d.id = re.decision_id
WHERE d.session_id = $1
GROUP BY re.rule_name
ORDER BY rule_name;
```

---

## 5. Maintenance & retention

### 5.1 State snapshot downsampling

A background job downsamples `state_snapshots` from 2 Hz to 1 Hz for sessions older than 30 days. The job is idempotent and respects per-study retention overrides.

- Job: _TBD_
- Schedule: nightly
- Owner: _TBD_

### 5.2 Recording retention

Per-study `retention_policy` in `studies.retention_policy` drives a nightly cleanup of mixed and per-track recordings in Cloudflare R2. The job sets `recording_url=NULL` first (soft-delete), and R2 lifecycle policies in `infra/r2/` perform the hard-delete after the configured grace period.

---

## 6. Deployment

### 6.1 Web (Vercel)

- Default branch (`main`) auto-deploys to production via the Vercel GitHub integration.
- PRs get preview deploys with env vars scoped to the `preview` env in Vercel project settings (preview points at the Railway preview Postgres + Redis).
- Rollback: in the Vercel dashboard, open Deployments → select a prior production deployment → "Promote to Production".
- Long-lived SSE: the SSE route declares `maxDuration: 300` (Vercel Pro 5-min ceiling). EventSource reconnects with `last-event-id`; the route backfills missed `decisions` rows from Postgres before resuming the live stream. Reconnect cycle is transparent to researchers.

### 6.2 Engine (Railway)

- Default branch (`main`) auto-deploys.
- Long-running sessions survive engine deploys via supervisor + state replay.
- Rollback: redeploy a prior commit; supervisor relaunches engines on the rolled-back image.

### 6.3 Database migrations

- Migrations live in `infra/postgres/migrations/` (Alembic, source of truth) and are applied via `alembic upgrade head` in CI against the target environment's `$DATABASE_URL`.
- The web Prisma client is regenerated via `prisma db pull` after migrations; CI fails on drift.
- Forward-only. Never edit a merged migration.
- Schema changes that affect shared types require regenerated TS (CI enforces via `shared-types:check`).

---

## 7. Compliance & PII handling (IRB-friendly)

- All session content is encrypted at rest (Railway Postgres default; Cloudflare R2 AES-256 default; customer-managed keys planned for Phase 7).
- Per-study retention policy is the single source of truth for how long content persists.
- Audit trail (`decisions`, `rule_evaluations`, `researcher_actions`) is preserved per the same study retention policy.
- Participant consent flow lives on `verbio-web`; consent records are stored separately and linked by `session_id` + `participant_id`.

---

## 8. Useful links

- [Engineering brief](../verbio-engineering-brief.md)
- [Architecture](./architecture.md)
- [Rules reference](./rules-reference.md)
- [ADRs](./adr/)
- Vercel dashboard (web): _TBD_
- Railway dashboard (engine + Postgres + Redis): _TBD_
- Cloudflare dashboard (R2 buckets): _TBD_
- LiveKit dashboard: _TBD_
- Resend dashboard (deliverability + bounces): _TBD_
- Sentry project (web): _TBD_
- Sentry project (engine): _TBD_
