# Verbio — Rules Reference

> The authoritative rule catalog and behavior contract. Implementations live in
> `services/engine/verbio_engine/rules/`. Every rule has table-driven unit tests covering
> fire and no-fire cases plus edge conditions (cooldown, missing data, malformed state).

---

## 1. Rule protocol

Every rule conforms to the `Rule` protocol (see brief §7.1):

```python
class Rule(Protocol):
    name: str                     # stable identifier
    version: str                  # bumped on any behavior change
    priority: int                 # higher wins on conflict
    default_cooldown_sec: float

    def predicate(self, state: SessionState, t: datetime) -> RulePredicateResult: ...
```

Each rule produces a `RulePredicateResult` containing:

- `fired: bool`
- `confidence: float` (0–1)
- `target_participant_id: str | None`
- `reason_codes: list[str]` — structured, machine-parsable
- `inputs_snapshot: dict` — the values the predicate read (for audit log)
- `proposed_action: ActionType`

---

## 2. Resolution order

When multiple rules fire on the same tick:

1. Filter out rules currently in cooldown.
2. Filter out rules suppressed by the global quietness budget.
3. Sort by `priority` desc, then `confidence` desc.
4. Winner becomes the decision; losers are persisted with `suppressed_reason="lower_priority_won"`.

---

## 3. V1 rule catalog

> All rules are versioned. Sessions snapshot the rule set version at start. Replay uses the snapshotted version — never let a config change retroactively alter historical interpretation.

### 3.1 `silence_gap`

- **Phase introduced:** 3
- **Action:** `prompt_participant`
- **Target:** least-recently-active participant
- **Predicate:** no one has spoken for `X` seconds AND no VAD activity
- **Default `X`:** 8.0 s
- **Default cooldown:** 45 s
- **Priority:** 50
- **Reason codes:** `silence_gap_<seconds>s`, `target_least_recent`
- **Tunable:** `silence_threshold_sec`, `cooldown_sec`

### 3.2 `speaker_imbalance`

- **Phase introduced:** 3
- **Action:** `prompt_participant`
- **Target:** under-share participant
- **Predicate:** one participant exceeds `Y ×` their fair share over last 5 min AND another is under `Z ×` fair share
- **Default `Y`:** 2.0
- **Default `Z`:** 0.4
- **Default cooldown:** 90 s
- **Priority:** 40
- **Reason codes:** `imbalance_dominator_<id>`, `imbalance_under_<id>`, `share_over_<pct>`, `share_under_<pct>`
- **Tunable:** `dominator_multiplier`, `under_multiplier`, `window_sec`, `cooldown_sec`

### 3.3 `topic_drift`

- **Phase introduced:** 3
- **Action:** `redirect_topic`
- **Target:** none (group redirect)
- **Predicate:** rolling 30 s transcript cosine similarity to study prompt embedding falls below threshold
- **Default threshold:** 0.55
- **Default cooldown:** 120 s
- **Priority:** 60
- **Reason codes:** `topic_similarity_<value>`, `drift_window_30s`
- **Tunable:** `similarity_threshold`, `window_sec`, `cooldown_sec`
- **Feature flag:** `FEATURE_TOPIC_DRIFT_RULE`

### 3.4 `cross_talk_pattern`

- **Phase introduced:** 3
- **Action:** `suggest_turn_taking`
- **Target:** none (group)
- **Predicate:** interruption events ≥ `N` in last `W` minutes
- **Default `N`:** 3, **Default `W`:** 2 min
- **Default cooldown:** 180 s
- **Priority:** 30
- **Reason codes:** `interruptions_<count>_in_<window>m`
- **Tunable:** `interruption_threshold`, `window_min`, `cooldown_sec`

### 3.5 `unheard_participant`

- **Phase introduced:** 3
- **Action:** `prompt_participant`
- **Target:** participant who hasn't spoken in `N` min but shows engagement
- **Predicate:** participant hasn't spoken in ≥ `N` min AND has positive engagement signals (backchannels > 0 OR `was_interrupted_count` increased recently)
- **Default `N`:** 4 min
- **Default cooldown:** 90 s
- **Priority:** 55
- **Reason codes:** `unheard_<id>_<minutes>m`, `engagement_signals_<count>`
- **Tunable:** `silence_min`, `engagement_signal_min`, `cooldown_sec`

### 3.6 `stalled_thread`

- **Phase introduced:** 5 (deferred from 3; depends on topic clustering)
- **Action:** `summarize_thread`
- **Target:** none
- **Predicate:** same topic cluster active > `M` minutes with no new sub-topics emerging
- **Default `M`:** 8 min
- **Default cooldown:** 300 s
- **Priority:** 35
- **Reason codes:** `stalled_<minutes>m`, `cluster_<id>`
- **Tunable:** `stall_min`, `subtopic_emergence_threshold`, `cooldown_sec`
- **Feature flag:** `FEATURE_STALLED_THREAD_RULE`

### 3.7 `time_remaining_pressure`

- **Phase introduced:** 3
- **Action:** `redirect_topic` (toward unaddressed prompt sub-question)
- **Target:** none (group redirect)
- **Predicate:** less than 10% of scheduled session time remains AND study prompt has unaddressed sub-questions
- **Default cooldown:** 240 s
- **Priority:** 70 (highest — time is a hard constraint)
- **Reason codes:** `time_left_<minutes>m`, `unaddressed_subq_<id>`
- **Tunable:** `time_pct_threshold`, `cooldown_sec`

---

## 4. Quietness budget (hard cap, applies to ALL rules)

A global throughput limiter, the strongest expression of the "bias toward silence" principle. Enforced _after_ rule resolution; the resolved decision is suppressed if any of the following are true:

- `current_window_count` ≥ `max_utterances_per_10min` (default **3**)
- Time since `last_utterance_at` < `min_seconds_between_utterances` (default **30 s**)

Suppression appears on the decision row as `suppressed_by=["quietness_budget"]`. Researchers can adjust the budget mid-session via the `set_quietness_budget` command.

---

## 5. Reason code conventions

- `snake_case`, machine-parsable.
- Include a numeric value where useful (`silence_gap_8s`, `topic_similarity_0.47`).
- Include participant references with IDs when the rule is targeted (`unheard_p3_4m`).
- Prefer multiple short codes over one descriptive one — easier to filter in replay.

---

## 6. Versioning a rule

When changing the **behavior** of a rule:

1. Bump `version` (e.g., `"1.0.0" → "1.1.0"`).
2. Update default thresholds if intended.
3. Update table-driven tests to cover new behavior.
4. Document the change in `CHANGELOG.md`.
5. Existing sessions continue to use the snapshot they started with — they do **not** see the new behavior.

When changing only configuration defaults (not the predicate itself), still bump the version. Replay must be reproducible.

---

## 7. Authoring a new rule

1. Define the predicate in plain language: "Fire when X AND Y, target Z."
2. Add the rule class under `services/engine/verbio_engine/rules/<name>.py` conforming to the `Rule` protocol.
3. Register it in the rule registry.
4. Add table-driven tests covering: fires when expected, doesn't fire when not, cooldown respected, targeting correct, malformed state handled.
5. Add an entry to this document.
6. Update brief §7.2 in the same PR (the brief is the canonical inventory).
7. If the rule introduces a new action type, update `ModeratorDecision.action` literal in `schemas/` and regenerate shared types.
