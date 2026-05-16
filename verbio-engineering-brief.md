# Verbio — Engineering Brief

**Audience:** Claude Code, acting as the lead engineer on a senior team.
**Document type:** Full project specification + phased delivery plan.
**Quality bar:** This is to be built the way a top-tier product engineering team at a research-tools company would build it. Code quality, architecture, observability, and testing are first-class concerns, not afterthoughts. No vibe-coded shortcuts that we'll regret in three months.

---

## 1. Product summary

Verbio is a real-time AI moderator for multi-participant qualitative research sessions (focus groups, user interviews, panel discussions). It joins a voice call, listens to all participants, and intervenes sparingly to keep the conversation productive — inviting quiet participants in, redirecting topic drift, defusing cross-talk, summarizing when useful.

The system has two non-negotiable product properties:

1. **The moderator is biased toward silence.** Default action every tick is `stay_silent`. The moderator must earn every word it says. Researchers using this tool have explicitly chosen it over a human moderator *because* they want minimal contamination of group dynamics.

2. **Every decision is auditable.** Researchers must be able to answer "why did the moderator say that?" and "why didn't it say something here?" in seconds. The system is not a black box; the audit trail is part of the product, not a debugging convenience.

The architectural commitment that delivers both: **separate the decision logic (deterministic, rule-based, fully logged) from the language generation (LLM-based, narrow scope, never decides whether or whom to speak to).** The LLM is a mouth, not a brain.

---

## 2. Non-goals

Be explicit about what we are not building:

- Not a transcription product. Transcription is infrastructure.
- Not a conversational AI agent. The moderator does not engage in dialogue with participants. It speaks, participants respond to each other, the moderator listens.
- Not a sentiment/emotion analysis platform. We may use sentiment as a *signal* in rules, but we don't surface it as a product feature.
- Not a meeting assistant. No action items, no summaries to email, no calendar integration.
- Not multi-tenant SaaS in v1. Single organization, internal researcher accounts.

---

## 3. Constraints and ground truth

- **Max participants per session:** 5 (plus moderator, plus observing researcher(s)).
- **Latency budget:** from end-of-rule-trigger to first audible word from moderator: ≤ 1500ms p95, ≤ 2500ms p99. Anything slower and the moderator interrupts the next speaker instead of filling silence.
- **Session length:** typically 30–90 minutes.
- **Moderator voice:** configurable per study.
- **Researcher role:** observe live, intervene live, review post-hoc, export data.
- **Recording:** full mixed audio + per-participant tracks retained per study-level policy.
- **Compliance posture:** assume sessions contain PII and may fall under IRB review. No third-party analytics on session content. Audio storage encrypted at rest. Configurable retention per study.

---

## 4. System architecture

### 4.1 High-level topology

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

### 4.2 Service boundaries

- **`verbio-web`** (Next.js, TypeScript): dashboard, auth, session lifecycle endpoints, replay API, exports. Owns no real-time decision logic.
- **`verbio-engine`** (Python 3.12, FastAPI for health/admin, LiveKit Agents SDK for room participation): the brain. Owns the tick loop, state, rules, decisions, LLM/TTS orchestration. One engine process per active session, spawned by a thin supervisor.
- **`verbio-shared`** (TypeScript + Python packages generated from a single source of truth): shared types for `ParticipantState`, `ModeratorDecision`, `RuleEvaluation`, `ResearcherCommand`. Use [Pydantic models exported to JSON Schema → TypeScript types via `json-schema-to-typescript`]. The two services must never drift on these shapes.

### 4.3 Why two languages

Python for the engine: better libraries for audio/ML (LiveKit Agents SDK is Python-first, Silero VAD, numpy for state math), and the rules engine reads more cleanly in Python.

TypeScript for the web: Next.js (App Router), Auth.js v5, Prisma client (read-mostly), and `EventSource` for live updates. Anything else is a regression.

The shared-types pipeline is the seam that keeps this honest.

---

## 5. Core domain model

These are the canonical shapes. Define them once in Pydantic, generate everything else.

### 5.1 `ParticipantState`

```python
class ParticipantState(BaseModel):
    participant_id: str
    display_name: str
    joined_at: datetime
    
    # Speaking metrics (rolling)
    speaking_time_total_sec: float
    speaking_time_last_5min_sec: float
    speaking_time_last_60sec: float
    turn_count: int
    last_spoke_at: datetime | None
    last_spoke_duration_sec: float | None
    
    # Engagement signals
    is_currently_speaking: bool
    vad_active: bool  # speech detected but not yet transcribed
    backchannel_count_last_2min: int  # "mm-hm", "yeah", short affirmations
    interruption_count: int  # times this participant cut someone off
    was_interrupted_count: int
    
    # Content signals
    recent_utterances: list[UtteranceRef]  # last 5, with timestamps
    rolling_transcript_2min: str  # for topic alignment checks
    
    # Derived flags (computed each tick)
    flags: ParticipantFlags
    
    # Fairness math
    fair_share_pct: float  # 100 / num_participants
    actual_share_last_5min_pct: float
```

`ParticipantFlags` is a set of booleans like `dominating`, `silent_too_long`, `frequently_interrupted`, `disengaged`. Each flag has a documented predicate and is recomputed every tick.

### 5.2 `ModeratorDecision`

```python
class ModeratorDecision(BaseModel):
    decision_id: UUID
    session_id: UUID
    tick_id: int
    timestamp: datetime
    
    action: Literal[
        "stay_silent",
        "prompt_participant",
        "redirect_topic",
        "summarize_thread",
        "request_clarification",
        "suggest_turn_taking",
        "close_session",
    ]
    target_participant_id: str | None
    
    # Provenance
    source: Literal["auto", "researcher_manual", "researcher_whisper"]
    triggering_rule: str | None  # rule name if auto
    researcher_id: str | None
    researcher_hint: str | None  # free text if manual
    
    # Explanation
    reason_codes: list[str]  # structured, e.g. ["silence_gap_8s", "p3_unheard_4min"]
    reason_human: str  # human-readable, generated for dashboard
    confidence: float  # 0–1, from rule output
    
    # Suppression info (even silent decisions log this)
    suppressed_by: list[str]  # ["quietness_budget", "global_cooldown"]
    
    # Execution
    was_executed: bool
    llm_prompt: dict | None  # exact input to mouth layer
    llm_output: str | None
    tts_audio_url: str | None
    spoken_at: datetime | None
    cooldown_until: datetime
```

### 5.3 `RuleEvaluation`

Logged for every rule, every tick, fired or not. This is the table that lets researchers ask "why didn't it intervene?"

```python
class RuleEvaluation(BaseModel):
    evaluation_id: UUID
    decision_id: UUID  # tick this belongs to
    rule_name: str
    rule_version: str  # rules are versioned; sessions snapshot the version
    fired: bool
    suppressed_reason: str | None  # "cooldown", "lower_priority_won", "disabled"
    predicate_inputs: dict  # snapshot of values the predicate read
    confidence: float
```

### 5.4 `ResearcherCommand`

Inbound to the engine.

```python
class ResearcherCommand(BaseModel):
    command_id: UUID
    session_id: UUID
    researcher_id: str
    issued_at: datetime
    command_type: Literal[
        "force_prompt",
        "force_redirect",
        "force_summary",
        "whisper",  # speak verbatim text
        "mute_moderator",
        "unmute_moderator",
        "pause_session",
        "resume_session",
        "set_quietness_budget",
        "flag_moment",
        "end_session",
    ]
    payload: dict  # command-specific
```

---

## 6. The tick loop

The engine runs at 2 Hz (configurable, 500ms tick interval). Each tick is a pure function of state → decision, plus side effects.

```
on_tick(t):
    # 1. Update state from event buffers
    state = state_store.advance_to(t)
    # state mutations from this tick onward are tracked for snapshot
    
    # 2. Drain researcher command queue
    commands = command_bus.drain(session_id)
    
    # 3. Evaluate rules (deterministic)
    evaluations = rules_engine.evaluate_all(state, t)
    
    # 4. Resolve decision
    if commands:
        decision = commands_to_decision(commands, state)
    else:
        decision = resolve_decision(evaluations, quietness_budget, cooldowns)
    
    # 5. Persist (even if stay_silent)
    persist_tick(state_snapshot=state, evaluations=evaluations, decision=decision)
    
    # 6. Execute if non-silent
    if decision.action != "stay_silent" and not moderator_muted:
        llm_output = mouth.generate(decision, state, persona)
        audio = tts.synthesize(llm_output, persona.voice_id)
        livekit.publish_audio(audio)
        mark_executed(decision, llm_output, audio_url, spoken_at)
        update_cooldowns_and_budget(decision)
    
    # 7. Emit realtime event for dashboard
    realtime.publish(session_id, "tick", decision_summary)
```

**Critical invariants:**
- Persistence happens before execution. A crash mid-tick must never leave a spoken utterance with no decision record.
- `stay_silent` decisions are persisted too. This is what makes the dashboard's "why is it quiet?" view possible.
- The tick loop never blocks on LLM/TTS. If they exceed the latency budget, the decision is logged as `was_executed=false` with `suppressed_by=["latency_exceeded"]` and the next tick proceeds.

---

## 7. Rules engine

### 7.1 Rule shape

Rules are first-class objects with a stable schema. No magic globals.

```python
class Rule(Protocol):
    name: str
    version: str
    priority: int  # higher wins on conflict
    default_cooldown_sec: float
    
    def predicate(self, state: SessionState, t: datetime) -> RulePredicateResult: ...

class RulePredicateResult(BaseModel):
    fired: bool
    confidence: float
    target_participant_id: str | None
    reason_codes: list[str]
    inputs_snapshot: dict  # what the predicate read, for the audit log
    proposed_action: ActionType
```

### 7.2 V1 rule set

Each rule has a config object loaded from the study's `RulesConfig`. Defaults shipped but everything tunable.

1. **`silence_gap`** — No one has spoken for X sec, no VAD activity. Targets least-recently-active participant. Default X = 8.0s. Cooldown 45s.

2. **`speaker_imbalance`** — One participant exceeds Y× their fair share over last 5min AND another is under Z× fair share. Default Y = 2.0, Z = 0.4. Targets the under-share participant. Cooldown 90s.

3. **`topic_drift`** — Rolling 30s transcript has cosine similarity < threshold to the study prompt embedding. Default threshold = 0.55. Action: `redirect_topic`. Cooldown 120s.

4. **`cross_talk_pattern`** — Interruption events ≥ 3 in last 2min. Action: `suggest_turn_taking`. Cooldown 180s.

5. **`unheard_participant`** — Participant hasn't spoken in N minutes AND has positive engagement signals (backchannels > 0 OR was_interrupted_count increased recently). Default N = 4min. Targets them. Cooldown 90s.

6. **`stalled_thread`** — Same topic cluster active for > 8min with no new sub-topics emerging. Action: `summarize_thread`. Cooldown 300s. (V1.1 — uses topic clustering, may defer to phase 5.)

7. **`time_remaining_pressure`** — Less than 10% of scheduled session time remains and the study prompt has unaddressed sub-questions. Action: `redirect_topic` toward unaddressed prompt. Cooldown 240s.

### 7.3 Decision resolution

When multiple rules fire on the same tick:
1. Filter to those not in cooldown.
2. Filter to those not suppressed by the quietness budget (see below).
3. Sort by priority desc, then confidence desc.
4. Winner becomes the decision; losers are logged with `suppressed_reason="lower_priority_won"`.

### 7.4 Quietness budget

A hard cap on moderator utterances per unit time, enforced globally regardless of how many rules want to fire.

```python
class QuietnessBudget(BaseModel):
    max_utterances_per_10min: int  # default 3
    min_seconds_between_utterances: float  # default 30.0
    current_window_count: int
    last_utterance_at: datetime | None
```

The budget is the strongest expression of the "bias toward silence" product principle. It must be live-adjustable by researchers via `set_quietness_budget` command.

### 7.5 Versioning

Rules are versioned. Each session snapshots the rule set version and config at start. Replay must use the snapshotted version to reconstruct decisions faithfully. Never let a config change retroactively affect historical session interpretation.

---

## 8. Mouth layer (LLM)

### 8.1 Scope

The LLM receives a structured decision and produces one sentence. It never sees:
- Full participant state objects.
- The rule logic or which rules fired.
- Other participants' transcripts beyond the immediate context window.
- Any directive to "decide what to say" — it only phrases what the engine already decided.

### 8.2 Prompt shape

```json
{
  "system": "<persona.style_prompt> You are the moderator of a research conversation. You speak rarely and briefly. You never introduce new topics or opinions. You phrase exactly the intervention specified, in one sentence, no preamble.",
  "user": {
    "intervention": "prompt_participant",
    "target_name": "Maya",
    "context": {
      "current_topic_summary": "remote work productivity tracking",
      "last_speaker_quote": "...nobody's tracking output anymore",
      "target_last_contribution_minutes_ago": 6,
      "target_engagement_note": "has been actively listening"
    },
    "constraints": {
      "max_sentences": 1,
      "address_target_by_name": true,
      "tone": "warm, inviting, not interrogative"
    }
  }
}
```

### 8.3 Model choice

- **Default:** DeepSeek `deepseek-chat` via the OpenAI-compatible API at `https://api.deepseek.com`. Cheap (~$0.27/M input, $1.10/M output) and sufficient for one-sentence outputs.
- **Configurable per study:** allow upgrading to `deepseek-reasoner` for studies where the moderator's reasoning quality matters more than latency (note: R1 emits thinking tokens; expect higher time-to-first-token).
- **Streaming:** stream tokens to TTS as they arrive for lowest latency to first audible word.
- **Latency caveat:** DeepSeek typically has higher and more variable time-to-first-token than tier-1 hosted models. The §8.4 templated-fallback path will trigger more often than with a low-latency provider — that's acceptable (fallback is real product, not a panic exit) and stays well inside the §6 1500 ms p95 / 2500 ms p99 end-to-end budget.

### 8.4 Failure modes

If the LLM call fails or exceeds 800ms wall clock:
- Fall back to a templated phrasing per action type (pre-written, persona-neutral).
- Log `llm_fallback=true` on the decision.
- Never skip the intervention silently — fallback over nothing.

---

## 9. TTS layer

- **Default provider:** Cartesia Sonic (lowest latency).
- **Fallback:** ElevenLabs Flash.
- **Voice selection:** curated library of 6–8 voices per provider, each pre-tagged with persona attributes (formality, warmth, pace). Researchers pick from this list, not from raw provider catalogs.
- **Audio publishing:** decoded PCM published as a LiveKit audio track from the moderator participant.
- **Caching:** the fallback templated phrasings can be pre-synthesized and cached per persona to make the LLM-failure path nearly instant.

---

## 10. Data layer

### 10.1 Postgres schema (Railway Postgres)

```sql
-- Studies are reusable session configurations
create table studies (
  id uuid primary key,
  org_id uuid not null,
  name text not null,
  prompt text not null,  -- the research question/prompt
  prompt_embedding vector(1536),  -- for topic drift
  rules_config jsonb not null,
  rules_version text not null,
  moderator_persona jsonb not null,
  retention_policy jsonb not null,
  created_at timestamptz default now(),
  created_by uuid not null
);

create table sessions (
  id uuid primary key,
  study_id uuid references studies(id),
  status text not null,  -- 'scheduled' | 'live' | 'ended' | 'aborted'
  scheduled_start timestamptz,
  actual_start timestamptz,
  actual_end timestamptz,
  livekit_room_name text not null unique,
  config_snapshot jsonb not null,  -- frozen copy of study config at start
  recording_url text,
  per_participant_recording_urls jsonb,
  created_at timestamptz default now()
);

create table participants (
  id uuid primary key,
  session_id uuid references sessions(id),
  display_name text not null,
  role text not null,  -- 'participant' | 'researcher' | 'moderator'
  joined_at timestamptz,
  left_at timestamptz,
  livekit_identity text not null
);

create table utterances (
  id uuid primary key,
  session_id uuid references sessions(id),
  participant_id uuid references participants(id),
  start_ts timestamptz not null,
  end_ts timestamptz not null,
  text text not null,
  confidence real,
  is_final boolean not null
);
create index on utterances (session_id, start_ts);

create table state_snapshots (
  id uuid primary key,
  session_id uuid references sessions(id),
  tick_id bigint not null,
  ts timestamptz not null,
  state jsonb not null  -- full SessionState at this tick
);
create index on state_snapshots (session_id, tick_id);

create table decisions (
  id uuid primary key,
  session_id uuid references sessions(id),
  tick_id bigint not null,
  ts timestamptz not null,
  action text not null,
  target_participant_id uuid references participants(id),
  source text not null,
  triggering_rule text,
  researcher_id uuid,
  researcher_hint text,
  reason_codes text[] not null,
  reason_human text not null,
  confidence real,
  suppressed_by text[],
  was_executed boolean not null,
  llm_prompt jsonb,
  llm_output text,
  tts_audio_url text,
  spoken_at timestamptz,
  cooldown_until timestamptz
);
create index on decisions (session_id, ts);
create index on decisions (session_id, was_executed) where was_executed = true;

create table rule_evaluations (
  id uuid primary key,
  decision_id uuid references decisions(id),
  rule_name text not null,
  rule_version text not null,
  fired boolean not null,
  suppressed_reason text,
  predicate_inputs jsonb,
  confidence real
);
create index on rule_evaluations (decision_id);

create table researcher_actions (
  id uuid primary key,
  session_id uuid references sessions(id),
  researcher_id uuid not null,
  ts timestamptz not null,
  command_type text not null,
  payload jsonb,
  resulting_decision_id uuid references decisions(id)
);

create table session_flags (
  id uuid primary key,
  session_id uuid references sessions(id),
  ts timestamptz not null,
  researcher_id uuid,
  note text,
  auto_generated boolean not null default false
);
```

### 10.2 Snapshot strategy

State snapshots every tick (2 Hz × 60 min = 7200 rows/session) is fine for Postgres. Keep them. Storage cost is trivial vs. analysis value. Add a retention job that downsamples older sessions to 1 Hz after 30 days.

### 10.3 Tenant scoping (no database-side RLS)

We do not use Postgres RLS. Railway Postgres is application-managed (not edge-served like Supabase), so the cost/benefit of in-database policies disappears. We enforce isolation at the query layer instead:

- Every table that holds tenant data carries an `org_id` column with a `not null` constraint and a partial index for join performance.
- Web access goes exclusively through a `scopedDb(orgId)` helper (a Prisma `$extends` client extension) that injects `where: { orgId }` on every read and rejects writes that would change `org_id`. Direct `prisma.<model>` calls outside the helper are forbidden by an ESLint rule (`no-restricted-syntax`) and fail CI.
- Engine commands arrive on Redis with the issuing researcher's `org_id`; the engine validates it against the session's `org_id` before applying, rejecting cross-org commands with an audit log entry.
- A nightly integration test seeds two orgs and asserts that every endpoint and every engine command refuses cross-org access. The test must pass for any change touching the data layer.

Tradeoff: cheaper to host and easier to reason about in app code, but enforcement lives in the codebase rather than the database. We accept the tradeoff explicitly and pay for it with mandatory coverage on the scoping helper. See ADR-0002.

---

## 11. Researcher dashboard

### 11.1 Live Control mode

Layout (desktop-first, single full-window view, no responsive degradation required for v1):

```
┌─────────────────────────────────────────────────────────────────┐
│ ● LIVE  Session: "Remote Work Study #4"  ⏱ 23:41  [End Session]│
├─────────────────────────────────────────────────────────────────┤
│ ┌─────────┬─────────┬─────────┬─────────┬─────────┐             │
│ │ Maya    │ Devon   │ Priya   │ Alex    │ Sam     │ Participant │
│ │ ●speaking│ silent  │ silent  │ silent  │ silent  │ tiles       │
│ │ 18% 5m  │ 32% 5m  │ 8% 5m ⚠ │ 25% 5m  │ 17% 5m  │             │
│ │ 0:04 ago│ 0:32 ago│ 4:12 ago│ 0:48 ago│ 1:15 ago│             │
│ └─────────┴─────────┴─────────┴─────────┴─────────┘             │
│                                                                 │
│ ┌───────────────────────────────┬─────────────────────────────┐ │
│ │ Live Transcript               │ Decision Log                │ │
│ │ Maya: ...so the tracking      │ 23:41:02 stay_silent        │ │
│ │ thing feels really invasive   │   silence_gap armed (4s)    │ │
│ │ to me, like —                 │ 23:40:45 stay_silent        │ │
│ │ Devon: yeah but how else      │   imbalance armed (cooldown)│ │
│ │ are managers supposed to —    │ 23:39:12 PROMPT Priya       │ │
│ │                               │   unheard_participant       │ │
│ │                               │   "you were nodding..."     │ │
│ └───────────────────────────────┴─────────────────────────────┘ │
│                                                                 │
│ ┌─ Why quiet now? ─────────────────────────────────────────┐    │
│ │ silence_gap: armed, 4s of 8s threshold                   │    │
│ │ imbalance: cooldown 47s remaining                        │    │
│ │ unheard: triggered 2m ago, cooldown 88s remaining        │    │
│ └──────────────────────────────────────────────────────────┘    │
│                                                                 │
│ ┌─ Control Bar ────────────────────────────────────────────┐    │
│ │ [Prompt…] [Redirect…] [Whisper…] [Flag Moment]           │    │
│ │ Quietness: ▬▬▬▬●▬▬▬▬▬  [🔇 Mute Moderator] [⏸ Pause]    │    │
│ └──────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

The "Why quiet now?" panel is the trust feature. It updates every tick. It shows researchers the system is reasoning continuously even when silent.

### 11.2 Replay & Analysis mode

Visually distinct (no red live indicator, different background hue). Timeline-first:

- Top: horizontal timeline spanning session length, with markers for each decision (color by action type), flags, and participant speech bands (5 stacked lanes).
- Scrubber controls audio playback. Click any marker → audio jumps + state panel updates.
- Middle: state snapshot panel for the selected moment.
- Right: decision detail panel — full reason codes, all rule evaluations for that tick (including non-firing rules), LLM prompt + output.
- Filters: action type, participant, rule, decision source.
- Export panel: transcript (.txt, .vtt), decision log (.csv), state snapshots (.jsonl), audio clips around flagged moments.

### 11.3 Realtime transport

Server-Sent Events (SSE) from Next.js, fed by a Redis pub/sub channel per session (`verbio:events:{session_id}`). The engine publishes lightweight event envelopes (`{type: "decision" | "state_snapshot" | "utterance", id, payload}`); a Next.js SSE route subscribes to the channel for the request's lifetime and streams to the dashboard via `EventSource`. On drop, the browser reconnects with the last received `event_id` and the server backfills missed rows from Postgres before resuming the live stream.

SSE rather than WebSocket because: the channel is read-only fan-out (researcher commands take a separate HTTP POST → Redis stream path), and `EventSource` handles auto-reconnect natively. We pay no protocol-design cost and avoid a custom WS server inside Next.js.

---

## 12. Repository structure

Monorepo. pnpm workspaces + a `services/engine` Python project. Turborepo for the JS side.

```
verbio/
├── apps/
│   └── web/                    # Next.js dashboard
├── services/
│   └── engine/                 # Python engine
│       ├── verbio_engine/
│       │   ├── agent.py        # LiveKit agent entrypoint
│       │   ├── tick_loop.py
│       │   ├── state/
│       │   ├── rules/
│       │   ├── mouth/
│       │   ├── tts/
│       │   ├── persistence/
│       │   └── commands/
│       ├── tests/
│       └── pyproject.toml
├── packages/
│   ├── shared-types/           # generated TS types
│   ├── ui/                     # shared React components
│   └── eslint-config/
├── schemas/                    # Pydantic source of truth → JSON Schema
├── infra/
│   ├── postgres/migrations/    # Alembic migrations (source of truth)
│   ├── railway/                # railway.toml + per-service definitions
│   └── r2/                     # Cloudflare R2 lifecycle policies
├── docs/
│   ├── architecture.md
│   ├── rules-reference.md
│   └── runbook.md
└── README.md
```

---

## 13. Quality gates

These are non-negotiable. No phase is complete until its gates pass.

- **TypeScript:** strict mode on, no `any` without an inline justification comment, ESLint with `@typescript-eslint/recommended-type-checked`.
- **Python:** `ruff` + `mypy --strict`. Pydantic for every boundary. No dict-typed payloads crossing module boundaries.
- **Testing:**
  - Engine: unit tests for every rule (table-driven, predicate inputs → expected RulePredicateResult). Tick loop has integration tests using a fake clock and synthetic event streams. Target ≥ 85% line coverage on `verbio_engine/rules/` and `verbio_engine/tick_loop.py`. Lower elsewhere is acceptable.
  - Web: component tests for control bar and decision log (React Testing Library). E2E with Playwright for the critical paths: start session, observe live decision, intervene manually, end session, open replay.
- **Observability:** structured logging (JSON) on every tick boundary. OpenTelemetry traces from `on_tick` → rule eval → LLM → TTS → publish. Sentry on both services.
- **CI:** GitHub Actions. Lint + typecheck + test on every PR. Block merge on red.
- **Migrations:** all schema changes via Alembic (`infra/postgres/migrations/`), reviewed in PR. The web Prisma client is regenerated via `prisma db pull` against the Alembic-managed schema; CI fails if the committed Prisma schema drifts from the database.
- **Secrets:** never committed. `.env.example` only. Production secrets split by host — web env vars live in Vercel project settings; engine, Postgres, and Redis env vars live in a single Railway project. Both surfaces support per-environment scoping (preview / staging / production).

---

## 14. Delivery phases

Each phase produces a working, demoable artifact. No phase ships without its quality gates passing.

### Phase 0 — Foundations (1 week)

**Goal:** repo, infra, CI, "hello world" services deployed.

- Monorepo scaffolding (pnpm, Turborepo, ruff, mypy, ESLint, Prettier).
- Railway project provisioned with two services (`engine`, `postgres`) and a Redis addon. `railway.toml` checked in.
- Vercel project provisioned for `apps/web`; GitHub integration wired for auto-deploy on `main` and preview deploys on PRs; env vars scoped per environment (preview / staging / production).
- Alembic migrations tooling wired in `services/engine`; first migration applies a `_health` table used by both services for connectivity smoke tests. `pnpm db:pull` regenerates `apps/web/prisma/schema.prisma` from the live database.
- Cloudflare R2 bucket provisioned; lifecycle policy stub committed to `infra/r2/`.
- Vercel deploy of empty Next.js app with Auth.js v5 (Postgres adapter over `DATABASE_URL_POOLED`) + Resend magic-link.
- Railway deploy of `verbio-engine` exposing `/health`.
- GitHub Actions CI: lint, typecheck, test, build on PR.
- Shared types pipeline: Pydantic → JSON Schema → TS types, with a CI check that fails if generated TS is out of date.
- `docs/architecture.md` committed with the diagram from §4.1.

**Done when:** an engineer can clone the repo, run `pnpm install && pnpm dev`, and see both services running locally with hot reload.

### Phase 1 — Audio plumbing & transcription (1.5 weeks)

**Goal:** join a LiveKit room as the moderator agent, transcribe all participants, persist utterances.

- LiveKit Cloud account, room provisioning via API.
- Engine joins room as moderator participant (no audio publishing yet).
- Per-participant audio track subscription.
- Deepgram streaming STT per track.
- VAD per track (Silero, local).
- Utterance persistence to Postgres with proper timing.
- Minimal web UI: create session, generate join links for participants, observe live transcript only (no moderator, no state, no decisions yet).
- Playwright E2E test that spins up 2 fake participants (pre-recorded audio loops) and asserts utterances land in the DB.

**Done when:** 5 humans can join a Verbio session and see their words transcribed in the dashboard in near-real-time.

### Phase 2 — State engine (1 week)

**Goal:** `ParticipantState` and `SessionState` updated correctly every tick, exposed live.

- Tick loop at 2 Hz with a fake clock for tests.
- All state fields from §5.1 implemented, each with a unit test.
- State snapshots persisted to Postgres every tick.
- Dashboard: participant tiles rendering live state (speaking time, last spoke, flags). No decisions yet.
- Property-based tests on state math (Hypothesis) — invariants like "sum of speaking_time across participants ≤ session elapsed time."

**Done when:** dashboard tiles update accurately during a live multi-participant session, and a researcher can watch dominance patterns emerge in real-time.

### Phase 3 — Rules engine in shadow mode (1.5 weeks)

**Goal:** all v1 rules implemented, evaluated every tick, decisions logged — but moderator stays silent.

- Rule protocol and registry.
- All seven v1 rules from §7.2 implemented with table-driven unit tests covering fire and no-fire cases per rule.
- Decision resolution (priority, cooldowns, quietness budget) with tests.
- Every tick writes a `decisions` row + N `rule_evaluations` rows.
- Dashboard: decision log panel + "Why quiet now?" panel, both live via the Next.js SSE route subscribed to `verbio:events:{session_id}` on Redis (engine publishes, route fans out to `EventSource`).
- Moderator never speaks in this phase. This is intentional. Researchers run real pilot sessions and watch what the engine *would* do.

**Done when:** a researcher can run a 30-minute pilot session and review the decision log post-hoc, agreeing with ≥ 70% of the would-be interventions. Disagreements feed rule tuning.

### Phase 4 — Mouth, TTS, and the moderator speaks (1.5 weeks)

**Goal:** end-to-end automated moderator.

- Mouth layer: DeepSeek client (via OpenAI-compatible SDK pointed at `https://api.deepseek.com`), prompt builder per action type, streaming token handling.
- TTS: Cartesia integration, voice library curation, audio publishing to LiveKit.
- Templated fallback phrasings per action type, pre-synthesized per persona, cached.
- Latency instrumentation: trace from rule fire → spoken_at, with the p95/p99 budget enforced as a CI perf test (synthetic).
- Persona configuration UI in study editor.

**Done when:** a 60-minute pilot session runs with the automated moderator and a researcher reports that the moderator spoke ≤ 6 times, all interventions felt appropriate, and the dashboard explained each one.

### Phase 5 — Researcher controls (1 week)

**Goal:** mid-session intervention.

- Command bus (Redis stream) for `ResearcherCommand`.
- All command types from §5.4 implemented end-to-end.
- Control bar UI: prompt/redirect/whisper modals, mute toggle, quietness slider, flag button, pause/resume.
- `researcher_actions` table populated for every command.
- Force commands generate full `ModeratorDecision` records with `source="researcher_manual"` and run through the same LLM + TTS pipeline.

**Done when:** a researcher can take full manual control of an automated session, blend manual + automatic interventions, and the audit trail in `decisions` cleanly distinguishes the two.

### Phase 6 — Replay & export (1 week)

**Goal:** post-session analysis is first-class.

- Recording egress: LiveKit composite + per-participant tracks to Cloudflare R2 (server-side egress from LiveKit Cloud to the R2 bucket; signed URLs issued by Next.js with short TTLs).
- Replay mode UI: timeline, scrubber, audio sync, state snapshot panel, decision detail panel with rule evaluations, filters.
- Export endpoints: transcript (.txt + .vtt), decision log (.csv), state snapshots (.jsonl), flagged audio clips (.mp3).
- Retention job: per-study policy enforcement, downsampling.

**Done when:** a researcher who didn't observe the session live can open the replay, understand what happened and why, and export everything they need for analysis.

### Phase 7 — Hardening (1 week)

**Goal:** production-ready.

- Load test: 10 concurrent sessions, 5 participants each, 60min. Engine memory and CPU stable.
- Chaos test: kill the engine mid-session, supervisor relaunches, session resumes from last persisted tick.
- Sentry, structured logs, alert thresholds tuned.
- Runbook in `docs/runbook.md`: how to diagnose stuck sessions, LLM/TTS provider outages, LiveKit egress failures.
- Security review: tenant-scoping audit (lint enforcement + cross-org integration tests + `scopedDb` coverage), secrets audit, dependency scan.
- IRB-friendly documentation: data flow diagram, retention controls, encryption posture.

**Done when:** the system can be handed off to another engineer with the runbook and they can operate it without tribal knowledge.

---

## 15. What to do first

When Claude Code picks this up, the first PR should be Phase 0 in its entirety — repo scaffolding, both services running locally, CI green, the shared-types pipeline working end-to-end. No application code. No rules. Just the bones, done correctly. That PR sets the tone for everything downstream.

Then proceed phase by phase. Do not skip ahead. Do not bundle phases. Each phase merges to main as one or more PRs, each PR is reviewable in under 30 minutes, and each phase ends with a tagged release.

---

## 16. Open questions to resolve before Phase 4

- Final voice library curation (who picks the 6–8 voices per provider?).
- Exact wording of templated fallback phrasings per action × persona.
- Study-level vs. org-level rule config defaults.
- IRB consent flow: where does the participant consent UI live? (Likely a pre-session page on `verbio-web`; spec separately before Phase 1 ships to production.)

These do not block early phases but must be resolved before the moderator speaks in production.
