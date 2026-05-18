# Verbio — Operational Runbook

On-call uses this as the primary reference for diagnosing and remediating live
issues. The "Common scenarios" section is ordered roughly by likelihood; each
scenario has a one-screen diagnostic flow and a remediation step. Diagnostic
queries against the production database appear in §4 and assume read-only
access via `psql $DATABASE_URL_ENGINE`.

---

## 1. Service map

| Service                      | Where                            | Logs                                     | Owner |
| ---------------------------- | -------------------------------- | ---------------------------------------- | ----- |
| `verbio-web`                 | Vercel                           | Vercel logs + Sentry (web project)       | _TBD_ |
| `verbio-engine`              | Railway (Dockerfile)             | Railway logs + Sentry (engine project)   | _TBD_ |
| Postgres                     | Railway Postgres (managed addon) | Railway dashboard + `pg_stat_statements` | _TBD_ |
| Redis                        | Railway Redis (managed addon)    | Railway dashboard                        | _TBD_ |
| Cloudflare R2                | Cloudflare account               | Cloudflare dashboard                     | _TBD_ |
| LiveKit Cloud                | LiveKit Cloud project            | LiveKit dashboard                        | _TBD_ |
| Resend (transactional email) | Resend account                   | Resend dashboard                         | _TBD_ |

---

## 2. On-call basics

> Phone-tree owners and channel names are placeholders until the team is
> staffed up; the rest of this section is operational and used today.

### Escalation policy

1. **First responder** acknowledges in the incident channel within 5 min.
2. If the incident affects an active session (researcher is live with
   participants) the first responder must reach out to the researcher within
   10 min via the contact recorded on the study (`studies.researcher_email`)
   and offer to extend the session or reschedule.
3. If unresolved after 30 min, page secondary. If unresolved after 60 min or
   PII is potentially exposed, page engineering lead and notify the customer
   contact on the affected `org`.

### Communication

- Internal incident channel: _TBD_
- Status page: _TBD_
- Customer-facing notice template lives in `docs/incident-comms.md` (created
  on first real incident).

### Severity definitions

| Sev   | Definition                                                                                   | Response                                  |
| ----- | -------------------------------------------------------------------------------------------- | ----------------------------------------- |
| Sev 1 | Live session degraded (moderator silent when it shouldn't be, audio cut, data loss)          | Page primary immediately; status page red |
| Sev 2 | New sessions cannot start; replay broken; researcher dashboard unreachable                   | Page primary; status page yellow          |
| Sev 3 | Background job failure (retention, recording dispatch), no live impact                       | Slack notify; fix in business hours       |
| Sev 4 | Single rule misbehaving in shadow mode; cosmetic UI bug; degraded but non-blocking telemetry | Triage in normal queue                    |

### Sentry triage

Both projects are configured by `verbio_engine/sentry.py` (engine) and
`apps/web/src/instrumentation.ts` (web). Sentry receives `ERROR`-level
records from the structured loggers via `LoggingIntegration` (engine) and
`@sentry/nextjs`'s server-side hook (web). PII is **off** on both projects
(`send_default_pii=False`) — events carry stack traces and structured fields
only, never transcript or participant audio.

Triage flow when Sentry pages:

1. Open the event; check `tags.environment` first (ignore preview/dev pages).
2. The `event` field on the log record is the canonical action name (e.g.
   `tick_loop.listener_failed`, `retention.purge_failed`,
   `livekit_webhook.egress_failed`, `clip_export.ffmpeg_failed`).
   Cross-reference §3 by that name.
3. If `session_id` is present in the breadcrumbs, jump straight to §4.1.

---

## 3. Common scenarios

### 3.1 Session is stuck — no decisions appearing in dashboard

Symptoms: researcher reports the decision log has stopped scrolling; tiles
show stale "speaking" indicators.

Diagnostic checklist:

- [ ] `SELECT status, actual_start, actual_end FROM sessions WHERE id = '<uuid>';` — confirm `status='live'`.
- [ ] Engine process alive? Check Railway logs for the session's engine
      worker (search `session_id=<uuid>`). Last log line within the last 2 s
      is healthy; > 10 s is suspect.
- [ ] Last persisted tick: see §4.1. If `last_decision_at` is > 5 s old on
      a `live` session, the engine has stalled.
- [ ] Redis command stream depth: `redis-cli XLEN verbio:commands:<session_id>`
      — non-zero means commands are queueing because the engine isn't consuming.
- [ ] SSE subscribers: `redis-cli PUBSUB NUMSUB verbio:events:<session_id>`
      should be ≥ 1 per connected researcher. Zero means no dashboard is
      attached — either no one is watching or the SSE route is failing
      (check Vercel logs for `sse.subscribe_failed`).

Remediation:

- If the engine has stalled (last tick stale), restart the worker (Railway
  → service → "Restart"). The supervisor relaunches the per-session worker,
  which loads the last `state_snapshot` and resumes from `tick_id + 1`. See
  §3.7 for the recovery contract.
- If Redis commands are accumulating but the engine is otherwise healthy,
  inspect the offending command shape — a malformed payload may be sitting
  at the head of the stream. Drain manually with
  `redis-cli XDEL verbio:commands:<session_id> <message-id>` only after
  capturing it for post-mortem.

### 3.2 Moderator did not speak when it should have

Open the **Replay** view for the session and navigate to the tick in question.
The `rule_evaluations` table answers "why didn't it?" — look for
`suppressed_reason` values:

- `cooldown` — the rule fired but was within its cooldown window.
- `lower_priority_won` — another rule with higher priority fired the same tick.
- `quietness_budget` — global cap hit.
- `latency_exceeded` — LLM/TTS exceeded the budget; decision logged but not executed.
- `disabled` — rule disabled via feature flag in study config.

Cross-reference with §4.3 to count suppressions by reason across the session.

### 3.3 Moderator spoke when it should not have

Open the decision row. Trace:

- `source` — `auto`, `researcher_manual`, or `researcher_whisper`?
- `triggering_rule` (if `auto`) — which rule fired?
- `reason_codes` — what predicate inputs triggered it?
- `llm_prompt` and `llm_output` — was the phrasing the issue (LLM drifted
  off-action) or was the decision itself wrong (rule misfired)?

If a rule misfired, file a `rules-version` bump rather than tweaking the live
config — config changes never retroactively reinterpret historical sessions.

### 3.4 LLM provider outage (DeepSeek)

Expected fallback behaviour:

- The mouth's wall-clock timeout (`DEEPSEEK_TIMEOUT_MS`, default 1200 ms) trips.
- Templated phrasing per `action` type is substituted from the per-persona cache.
- Decision row records `llm_fallback=true`.
- No intervention is ever silently skipped — fallback over nothing.

If templated audio cache is missing for a persona, log error and emit a
generic-voice pre-synthesised phrasing. Backfill the cache offline.

Sustained failure cluster (≥ 20 % fallback rate across a session window):
check `https://status.deepseek.com`. If the outage is provider-wide, the
researcher can switch the per-study `DEEPSEEK_MODEL_DEFAULT` to a static
cached phrasing for the remainder of the session via the study editor; the
change takes effect at the next tick boundary.

### 3.5 TTS provider outage (Cartesia → ElevenLabs fallback)

- Primary Cartesia call fails or exceeds budget.
- Engine switches to ElevenLabs Flash automatically (`tts_fallback=true` on
  the decision row).
- `decisions.tts_audio_url` reflects which provider served the clip (URL host).
- If both fail, the templated pre-synthesised audio is used (cached per persona).

If both providers are unavailable and the persona cache is cold, the engine
will publish a sub-1s silence and mark `was_executed=false` with
`suppressed_by=["tts_unavailable"]` rather than block the tick loop.

### 3.6 LiveKit egress / recording failure

- The session continues; live behaviour is unaffected.
- Replay will be unavailable for the affected time window.
- `sessions.recording_url` stays NULL on a failed mixed-egress dispatch,
  and `sessions.per_participant_recording_urls` is missing the affected
  tracks. Check the LiveKit webhook handler logs (`recordings.webhook.*`
  in Sentry) for the upstream failure code.
- Re-trigger egress for completed sessions via LiveKit API (see §7.2).

### 3.7 Engine crash mid-session

- Supervisor detects exit and relaunches the engine process.
- Engine loads the last `state_snapshot` for the session and resumes ticking
  from `tick_id + 1`.
- All persisted decisions remain authoritative; nothing is replayed or
  re-executed (persistence is committed **before** mouth/TTS dispatch per
  brief §6, so an interrupted tick is recorded as `was_executed=false`).
- Researcher dashboard reconnects automatically via `EventSource` retry
  (using `last-event-id`); the SSE route backfills missed `decisions` rows
  from Postgres before resuming the live stream.

Chaos test that exercises this path lands in P7 L7. Once it ships,
re-running it post-incident validates the recovery path still holds against
the current persistence shape.

### 3.8 Dashboard offline (web outage)

- Sessions continue uninterrupted — the engine does not depend on `verbio-web`.
- Researcher commands cannot be issued during the outage.
- After recovery, the dashboard re-subscribes (`EventSource` reconnect with
  `last-event-id`) and the SSE route backfills missed `decisions` rows from
  Postgres before resuming live.

### 3.9 Tenant boundary suspected breach

`scopedDb` (P7 L1) refuses cross-org access by filtering at the query layer
— a foreign `orgId` on a lookup returns null/empty rather than the row.
This makes a breach silent at the SDK boundary; the symptoms surface
upstream (researcher reports "I can see a study I don't own", or a row
referenced by a webhook doesn't exist for the calling org).

Investigation flow:

1. Identify the calling code path from the stack trace or request log.
2. Confirm the calling user's `orgId` against the resource's `orgId` using
   §4.6 (any session with > 1 owning org is a bug).
3. If the breach was caused by a code bug bypassing `scopedDb`, file Sev 1
   — tenant isolation is a P0 contract.
4. If the symptom was a session created under the wrong `orgId`, inspect
   the session-creation flow.

Defensive layers in place:

- Lint rule (P7 L1) bans direct `prisma.<model>` access outside the facade.
- CI integration tests in `apps/web/test/integration/scoped-db.integration.test.ts`
  (P7 L3) prove the boundary on every PR.
- A future improvement is to emit `db.scoped.cross_tenant_attempt` Sentry
  events from inside the facade when a foreign-org lookup hits — wire that
  the first time we see this incident class in production.

### 3.10 Latency budget breach (p95 > 1500 ms)

The synthetic perf test in `services/engine/tests/perf/` runs on every PR
post-Phase 4. If it fails in CI:

1. Inspect the regression — usually a mouth or TTS provider slowdown.
2. Check `decisions.spoken_at - decisions.ts` distribution for the most
   recent session window (§4.4).
3. If the regression is upstream-provider-driven, raise the action in the
   incident channel — the budget is not negotiable per brief §6.

---

## 4. Diagnostic queries

All queries are read-only. Run against `$DATABASE_URL_ENGINE` (engine's
primary connection) for live sessions or `$DATABASE_URL_POOLED` (pgBouncer
transaction-mode endpoint) from web for replay analysis.

### 4.1 Stuck session — last decision per live session

```sql
SELECT
    s.id AS session_id,
    s.status,
    max(d.ts) AS last_decision_at,
    now() - max(d.ts) AS staleness
FROM sessions s
LEFT JOIN decisions d ON d.session_id = s.id
WHERE s.status = 'live'
GROUP BY s.id, s.status
ORDER BY staleness DESC NULLS FIRST;
```

`staleness > 5 s` on a `live` session = engine stalled.

### 4.2 Decision timeline for a session

```sql
SELECT
    tick_id,
    ts,
    action,
    target_participant_id,
    source,
    triggering_rule,
    was_executed,
    suppressed_by,
    array_length(reason_codes, 1) AS reason_count
FROM decisions
WHERE session_id = $1
ORDER BY tick_id ASC;
```

### 4.3 Why was it quiet — suppressions by reason

```sql
SELECT
    re.rule_name,
    count(*)                                     AS evaluations,
    count(*) FILTER (WHERE re.fired)             AS fires,
    count(*) FILTER (WHERE re.suppressed_reason = 'cooldown')          AS cooldowned,
    count(*) FILTER (WHERE re.suppressed_reason = 'lower_priority_won') AS lost_priority,
    count(*) FILTER (WHERE re.suppressed_reason = 'quietness_budget')  AS budget_capped,
    count(*) FILTER (WHERE re.suppressed_reason = 'latency_exceeded')  AS latency_dropped,
    count(*) FILTER (WHERE re.suppressed_reason = 'disabled')          AS disabled
FROM rule_evaluations re
JOIN decisions d ON d.id = re.decision_id
WHERE d.session_id = $1
GROUP BY re.rule_name
ORDER BY re.rule_name;
```

### 4.4 Latency distribution — spoken decisions only

```sql
SELECT
    percentile_cont(0.50) WITHIN GROUP (ORDER BY ms) AS p50_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY ms) AS p95_ms,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY ms) AS p99_ms,
    count(*) AS n
FROM (
    SELECT EXTRACT(EPOCH FROM (spoken_at - ts)) * 1000 AS ms
    FROM decisions
    WHERE was_executed
      AND spoken_at IS NOT NULL
      AND ts > now() - interval '1 hour'
) sub;
```

p95 > 1500 ms over a 1-hour window violates the brief §6 budget — page on it.

### 4.5 LLM / TTS fallback rate

```sql
SELECT
    count(*) FILTER (WHERE was_executed)                                       AS executed,
    count(*) FILTER (WHERE was_executed AND (llm_prompt->>'fallback')::bool)   AS llm_fallbacks,
    count(*) FILTER (WHERE was_executed AND tts_audio_url LIKE '%elevenlabs%') AS tts_fallbacks
FROM decisions
WHERE session_id = $1;
```

`llm_fallbacks / executed > 0.20` sustained = DeepSeek degraded (see §3.4).

### 4.6 Tenant boundary spot-check

Ownership chains through `studies.org_id` — sessions inherit via
`sessions.study_id`. A leak shows up as a session attached to multiple
studies (impossible by FK) or a decision whose ultimate `org_id` doesn't
match the caller's. The check below confirms the chain is intact.

```sql
-- All decisions in the last 24h alongside their owning org_id.
-- Use this to verify a researcher's complaint that they "see another
-- org's data" — the owning org should always match their own.
SELECT
    d.id           AS decision_id,
    d.session_id,
    s.study_id,
    st.org_id      AS owning_org
FROM decisions d
JOIN sessions s ON s.id = d.session_id
LEFT JOIN studies st ON st.id = s.study_id
WHERE d.ts > now() - interval '24 hours'
  AND st.org_id <> '<expected_org_uuid>'  -- replace with caller's orgId
LIMIT 50;
```

A non-empty result means a session was attached to a study owned by a
different org — file Sev 1 and inspect the session-creation path.

---

## 5. Alert thresholds

Configure these in Sentry (project → Alerts) and in Railway (service → Metrics)
respectively. Thresholds are starting values — tune from real traffic baselines.

### 5.1 Sentry

| Alert                           | Threshold                                | Severity |
| ------------------------------- | ---------------------------------------- | -------- |
| `tick_loop.listener_failed`     | ≥ 3 events / 5 min                       | Sev 2    |
| `tick_loop.tick_overrun`        | ≥ 50 events / 5 min                      | Sev 3    |
| `livekit_webhook.egress_failed` | ≥ 1 event                                | Sev 3    |
| `livekit_webhook.unknown_room`  | ≥ 5 events / 5 min                       | Sev 2    |
| `clip_export.ffmpeg_failed`     | ≥ 3 events / 10 min                      | Sev 3    |
| `retention.purge_failed`        | ≥ 1 event                                | Sev 3    |
| Any unhandled exception, engine | error rate > 0.5 % of ticks over 10 min  | Sev 2    |
| Any unhandled exception, web    | error rate > 1 % of requests over 10 min | Sev 2    |

### 5.2 Railway (engine)

| Metric           | Threshold                             | Severity |
| ---------------- | ------------------------------------- | -------- |
| Memory per pod   | > 500 MB sustained 5 min              | Sev 3    |
| CPU per pod      | > 80 % sustained 5 min                | Sev 2    |
| Pod restart rate | > 3 restarts / 10 min on the same pod | Sev 2    |
| Postgres CPU     | > 80 % sustained 5 min                | Sev 2    |
| Redis memory     | > 75 % of plan limit                  | Sev 3    |

### 5.3 LiveKit Cloud

| Metric                            | Threshold         | Severity |
| --------------------------------- | ----------------- | -------- |
| Egress job failure rate           | > 5 % over 1 hour | Sev 2    |
| Room participant publish failures | > 1 % over 10 min | Sev 2    |

---

## 6. Maintenance & retention

### 6.1 State snapshot downsampling

Job: `apps/web/src/app/api/admin/retention/run/route.ts` (POST, requires
admin token). Downsamples `state_snapshots` from 2 Hz to 1 Hz for sessions
older than 30 days. Idempotent; respects per-study `retention_policy`.
Triggered nightly via Vercel Cron (`vercel.json` → `crons`).

Manual run: `curl -X POST -H "Authorization: Bearer $VERBIO_ENGINE_ADMIN_TOKEN" https://<host>/api/admin/retention/run`.

### 6.2 Recording retention

Per-study `studies.retention_policy` drives nightly cleanup of mixed and
per-track recordings in Cloudflare R2. The job sets `recording_url=NULL`
first (soft-delete); R2 lifecycle policies in `infra/r2/` perform the
hard-delete after the configured grace period.

### 6.3 Manual session termination

If a session must be ended administratively (e.g. researcher unreachable,
participant safety incident):

```sql
-- Mark the session ended; the engine's next tick observes the status flip
-- and stops cleanly, persisting a final state_snapshot.
UPDATE sessions
SET status = 'ended', actual_end = now()
WHERE id = '<uuid>' AND status = 'live';
```

The engine polls `sessions.status` once per tick; expect a clean shutdown
within ~500 ms. If the engine is wedged and does not observe the flip,
restart the worker (§3.1 remediation).

---

## 7. Deployment & rollback

### 7.1 Web (Vercel)

- Default branch (`main`) auto-deploys to production via the Vercel GitHub
  integration.
- PRs get preview deploys with env vars scoped to the `preview` env
  (preview points at the Railway preview Postgres + Redis).
- Rollback: Vercel dashboard → Deployments → select a prior production
  deployment → "Promote to Production".
- Long-lived SSE: the SSE route declares `maxDuration: 300` (Vercel Pro
  5-min ceiling). `EventSource` reconnects with `last-event-id`; the route
  backfills missed `decisions` rows from Postgres before resuming. Reconnect
  cycle is transparent to researchers.

### 7.2 Engine (Railway)

- Default branch (`main`) auto-deploys.
- Long-running sessions survive engine deploys via supervisor + state
  replay (§3.7).
- Rollback: redeploy a prior commit; supervisor relaunches engines on the
  rolled-back image.
- Force-restart a single session's worker via Railway dashboard → service
  → Restart (drains gracefully — current tick completes first).

Re-triggering a LiveKit egress for a completed session:

```bash
# Requires LIVEKIT_API_KEY + LIVEKIT_API_SECRET from the engine's env.
livekit-cli egress start \
  --room "<livekit_room_name>" \
  --output "s3://<r2-bucket>/sessions/<session_id>/replay.mp4"
```

### 7.3 Database migrations

- Migrations live in `infra/postgres/migrations/` (Alembic, source of
  truth) and are applied via `alembic upgrade head` in CI against the
  target environment's `$DATABASE_URL`.
- The web Prisma client is regenerated via `prisma db pull` after
  migrations; CI fails on drift.
- **Forward-only.** Never edit a merged migration. To undo a change, ship
  a new migration that inverts it.
- Schema changes that affect shared types require regenerated TS (CI
  enforces via `shared-types:check`).

Migration smoke test before promoting to prod:

```bash
# In a preview environment with realistic data volume:
uv run alembic upgrade head
uv run pytest tests/persistence -x   # smoke any model changes
pnpm --filter @verbio/web prisma db pull
pnpm --filter @verbio/web tsc --noEmit
```

### 7.4 Pre-release load validation

The concurrent-session load test models the brief's §14 Phase 7 target
(ten rooms, five participants each, sixty minutes). It lives at
`services/engine/tests/load/test_concurrent_sessions.py` and is excluded
from the default sweep via `-m "not load"`.

CI runs a 30-second smoke on every PR to catch obvious regressions:

```bash
cd services/engine
uv run pytest -m load --no-cov
```

Before tagging a release, run the full 60-minute soak on a beefy host
(local laptop is fine; the engine is single-process Python):

```bash
cd services/engine
VERBIO_LOAD_DURATION_SEC=3600 uv run pytest -m load --no-cov -s
```

Pass criteria the test enforces:

- Zero listener failures.
- Tick-overrun rate < 2 % of total ticks.
- Worst-session p95 per-tick latency < 100 ms.
- RSS growth < 250 MB (10 sessions x ~25 MB headroom each).

A clean run prints a one-line summary (`load summary: sessions=10, ...`)
even on success — paste it into the release notes so the numbers are on
record. A failure here blocks the release; investigate before retrying
(common causes: event-log retention regression in `StateStore`,
non-idempotent rule that allocates per tick, listener that holds a
reference to the prior snapshot).

Reproducibility: `VERBIO_LOAD_SEED` seeds the per-session RNG so a
flaky failure can be re-run against the same load shape.

---

## 8. Compliance & PII handling (IRB-friendly)

- All session content is encrypted at rest (Railway Postgres default;
  Cloudflare R2 AES-256 default; customer-managed keys planned, see
  `docs/data-flow.md` once it lands in P7 L9).
- Per-study retention policy is the single source of truth for how long
  content persists.
- Audit trail (`decisions`, `rule_evaluations`, `researcher_actions`) is
  preserved per the same study retention policy.
- Participant consent flow lives on `verbio-web`; consent records are stored
  separately and linked by `session_id` + `participant_id`.
- Sentry is configured with `send_default_pii=False` on both services —
  participant audio, transcripts, and consent records never leave the
  database boundary for the error tracker.

---

## 9. Useful links

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

---

## Appendix A — Common commands

Engine (run from `services/engine/`):

```bash
uv run alembic upgrade head              # apply migrations
uv run alembic history --verbose         # migration log
uv run pytest --no-cov                   # full engine test sweep
uv run pytest -m load --no-cov           # 30 s load smoke (CI variant)
VERBIO_LOAD_DURATION_SEC=3600 \
  uv run pytest -m load --no-cov -s      # 60-min pre-release soak
uv run mypy --strict verbio_engine       # strict typecheck
uv run ruff check verbio_engine tests    # lint
uv run python -m verbio_engine.cli.schema_export  # regenerate shared types
```

Web (run from repo root):

```bash
pnpm --filter @verbio/web tsc --noEmit   # typecheck
pnpm --filter @verbio/web lint           # lint
pnpm --filter @verbio/web test           # vitest
pnpm --filter @verbio/web prisma db pull # refresh Prisma from live schema
pnpm --filter @verbio/web playwright test # e2e
```

Postgres (read-only diagnostics):

```bash
psql $DATABASE_URL_ENGINE                # interactive
psql $DATABASE_URL_ENGINE -f script.sql  # batch
```

Redis (command bus + SSE pub/sub):

```bash
redis-cli -u $REDIS_URL
> XLEN verbio:commands:<session_id>          # backlog of researcher commands
> PUBSUB NUMSUB verbio:events:<session_id>   # how many SSE clients attached
> XRANGE verbio:commands:<session_id> - +    # peek the command stream (drains nothing)
```
