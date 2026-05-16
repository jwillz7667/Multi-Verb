# Verbio Dashboard — UI Build Prompt

You are a senior product designer-engineer building the frontend for **Verbio**, an AI-moderated research conversation platform. Build the full dashboard UI as a production-quality Next.js 15 application using React 19, TypeScript (strict), Tailwind CSS 4, and shadcn/ui. This is the UI a top-tier product team at a research-tools company (think Dovetail, Maze, UserTesting) would ship. Code quality, accessibility, and visual polish are first-class concerns.

You are building UI only. Wire all data to a mock data layer (`/lib/mock-data.ts`) that returns realistic fixtures. Use React Server Components where appropriate, Client Components for anything interactive. No real backend, no real WebSockets — simulate live updates with `setInterval` against the mock data.

---

## 1. Product context (read this fully before coding)

Verbio is a real-time AI moderator that joins multi-participant voice conversations (focus groups, user interviews, panels — up to 5 participants). It listens, tracks per-participant state, and intervenes sparingly using deterministic rules. An LLM only phrases what the rules decided. Researchers observe live, can intervene mid-session, and review everything afterward.

Two product principles drive every UI decision:

1. **The moderator is biased toward silence.** UI must make researchers feel the moderator is reasoning continuously, even when not speaking. Surface *why it's quiet*, not just what it said.

2. **Every decision is auditable.** Researchers must answer "why did it speak?" and "why didn't it speak there?" in seconds. The audit trail is the product, not a debug feature.

The dashboard has four primary surfaces:

- **Study list** — researcher's home, lists past and scheduled studies
- **Study setup** — configure a study (prompt, rules, persona, participants)
- **Live Control** — observe and intervene during an active session
- **Replay & Analysis** — post-session timeline-driven review

Plus auth (Auth.js v5 magic-link via Resend), a session pre-join page for participants, and a settings area.

---

## 2. Stack and conventions

**Required:**
- Next.js 15 (App Router), React 19, TypeScript strict mode
- Tailwind CSS 4, shadcn/ui (install via CLI as needed)
- `lucide-react` for icons (outline style, 16–20px inline)
- `recharts` for any data viz beyond the custom timeline
- `framer-motion` for subtle transitions only (no flashy animation)
- `zod` for all form validation
- `react-hook-form` for forms
- `date-fns` for time formatting
- `clsx` + `tailwind-merge` (export `cn` helper)

**Forbidden:**
- No `any` without an inline justification comment
- No emoji in UI text
- No gradients, drop shadows beyond functional focus rings, no neon, no glassmorphism
- No font weights other than 400 and 500 (no 600/700 — too heavy)
- No Title Case in UI copy — always sentence case
- No mid-sentence bolding
- No `position: fixed` for tooltips/popovers — use Radix primitives via shadcn

**File structure:**
```
app/
  (auth)/login/page.tsx
  (app)/
    layout.tsx                    // app shell with sidebar
    studies/page.tsx              // study list
    studies/new/page.tsx          // study creation flow
    studies/[id]/page.tsx         // study detail / sessions list
    sessions/[id]/live/page.tsx   // Live Control
    sessions/[id]/replay/page.tsx // Replay & Analysis
    settings/page.tsx
  join/[token]/page.tsx           // participant pre-join (public)
components/
  ui/                             // shadcn primitives
  app/
    sidebar.tsx
    participant-tile.tsx
    decision-log.tsx
    why-quiet-panel.tsx
    control-bar.tsx
    timeline.tsx
    state-snapshot-panel.tsx
    decision-detail-panel.tsx
    rule-evaluation-list.tsx
    flag-button.tsx
    intervention-modal.tsx
    persona-picker.tsx
    rule-config-editor.tsx
lib/
  mock-data.ts                    // all fixtures
  mock-realtime.ts                // setInterval-driven live updates
  types.ts                        // see §4
  utils.ts                        // cn, formatters
hooks/
  use-session-state.ts
  use-decision-stream.ts
  use-keyboard-shortcuts.ts
```

---

## 3. Visual language

This is the spec. Don't deviate without strong reason.

**Palette** (define in `globals.css` as CSS variables, light + dark mode):
- Surfaces: white (`--bg-primary`), warm off-white `#FAFAF7` (`--bg-secondary`), light gray `#F4F3EE` (`--bg-tertiary`)
- Text: near-black `#1A1A19` primary, `#5F5E5A` secondary, `#888780` tertiary
- Borders: 0.5px solid `rgba(0,0,0,0.08)` default, `rgba(0,0,0,0.15)` emphasis
- Semantic: blue `#378ADD` (info/auto-decisions), amber `#BA7517` (warning/dominating), red `#A32D2D` (danger/end session), green `#3B6D11` (success/fired-rule), purple `#534AB7` (redirect actions)
- Each semantic color has matching background variants at ~10% opacity for badges/fills

**Typography:**
- Font stack: Inter (sans), JetBrains Mono (mono) — both via `next/font/google`
- h1 22px / 500, h2 18px / 500, h3 16px / 500
- Body 14px / 400, line-height 1.5
- Small/meta 12px, micro 11px (labels, timestamps)
- Mono for timestamps, tick IDs, rule names in the audit trail — never for body

**Spacing & shape:**
- Radius: 6px default, 8px cards, 10px primary surfaces
- Card padding: `p-4` (16px) standard, `p-5` for primary panels
- Section spacing: `space-y-4` between siblings, `space-y-6` between major sections
- Grid gaps: 8px tight, 12px standard, 16px loose

**Borders:**
- Always 0.5px (use `border-[0.5px]`). Never 1px borders except for the 2px featured-card accent
- Single-sided borders (`border-l-2` etc.) get `rounded-none` on that side

**Density:**
- Live Control and Replay are information-dense. Don't over-pad. A researcher monitoring a 60-min session shouldn't have to scroll.
- Study list and settings can breathe more.

---

## 4. Type definitions

Put these in `lib/types.ts`. The mock data layer must conform.

```ts
export type Participant = {
  id: string;
  displayName: string;
  initials: string;
  colorRamp: 'blue' | 'amber' | 'pink' | 'green' | 'teal' | 'purple';
  joinedAt: string; // ISO
  leftAt: string | null;
};

export type ParticipantFlag = 'dominating' | 'silent_too_long' | 'frequently_interrupted' | 'disengaged';

export type ParticipantState = {
  participantId: string;
  speakingTimeTotalSec: number;
  speakingTimeLast5MinSec: number;
  speakingTimeLast60SecSec: number;
  turnCount: number;
  lastSpokeAt: string | null;
  isCurrentlySpeaking: boolean;
  vadActive: boolean;
  backchannelCountLast2Min: number;
  interruptionCount: number;
  wasInterruptedCount: number;
  fairSharePct: number;
  actualShareLast5MinPct: number;
  flags: ParticipantFlag[];
};

export type DecisionAction =
  | 'stay_silent'
  | 'prompt_participant'
  | 'redirect_topic'
  | 'summarize_thread'
  | 'request_clarification'
  | 'suggest_turn_taking'
  | 'close_session';

export type DecisionSource = 'auto' | 'researcher_manual' | 'researcher_whisper';

export type Decision = {
  id: string;
  sessionId: string;
  tickId: number;
  timestamp: string;
  action: DecisionAction;
  targetParticipantId: string | null;
  source: DecisionSource;
  triggeringRule: string | null;
  researcherId: string | null;
  researcherHint: string | null;
  reasonCodes: string[];
  reasonHuman: string;
  confidence: number;
  suppressedBy: string[];
  wasExecuted: boolean;
  llmOutput: string | null;
  spokenAt: string | null;
  cooldownUntil: string;
};

export type RuleEvaluation = {
  id: string;
  decisionId: string;
  ruleName: string;
  ruleVersion: string;
  fired: boolean;
  suppressedReason: string | null;
  predicateInputs: Record<string, unknown>;
  confidence: number;
};

export type Utterance = {
  id: string;
  sessionId: string;
  participantId: string;
  startTs: string;
  endTs: string;
  text: string;
  confidence: number;
  isFinal: boolean;
};

export type Session = {
  id: string;
  studyId: string;
  studyName: string;
  status: 'scheduled' | 'live' | 'ended' | 'aborted';
  scheduledStart: string;
  actualStart: string | null;
  actualEnd: string | null;
  participants: Participant[];
  durationSec: number; // for ended sessions
};

export type ModeratorPersona = {
  id: string;
  name: string;
  voiceId: string;
  provider: 'cartesia' | 'elevenlabs';
  stylePrompt: string;
  pace: 'slow' | 'normal' | 'brisk';
  formality: 'formal' | 'neutral' | 'casual';
  previewAudioUrl: string;
};

export type RuleConfig = {
  name: string;
  enabled: boolean;
  priority: number;
  cooldownSec: number;
  parameters: Record<string, number | string | boolean>;
};

export type Study = {
  id: string;
  name: string;
  prompt: string;
  rulesConfig: RuleConfig[];
  persona: ModeratorPersona;
  quietnessBudget: {
    maxUtterancesPer10Min: number;
    minSecondsBetweenUtterances: number;
  };
  retentionDays: number;
  createdAt: string;
  sessionCount: number;
};
```

---

## 5. App shell

A persistent left sidebar (220px wide, collapsible to 56px icons-only), main content area fills the rest.

**Sidebar contents:**
- Top: small Verbio wordmark (custom SVG, lowercase, weight 500). No tagline.
- Nav: Studies (default), Sessions, Insights (disabled, "coming soon" pill), Settings
- Bottom: org switcher, user avatar with dropdown (profile, sign out)

**Main area:**
- Top bar: 48px tall, breadcrumb on the left, contextual actions on the right
- Content area: scrollable, max-width `max-w-screen-2xl` centered with `px-6 py-6`

Use shadcn `<Sidebar>` from the `sidebar-07` block as a base, customize heavily.

---

## 6. Surface 1 — Study list (`/studies`)

The home page. A table-driven list of studies with quick filters.

**Layout:**
- Page header: "Studies" h1, subtitle "Configure and run moderated research sessions", a primary "New study" button on the right
- Filter bar: search input, status filter (All / Active / Draft / Archived), sort dropdown
- Table with columns: name, prompt preview (truncated), sessions count, last session date, status pill, actions menu
- Empty state when filtered to nothing: centered illustration (simple line SVG), heading, brief copy, CTA

**Interactions:**
- Row hover: subtle bg change, cursor pointer
- Click row → navigate to `/studies/[id]`
- Actions menu: edit, duplicate, archive, delete (with confirm dialog)

Mock at least 8 studies with varied states.

---

## 7. Surface 2 — Study setup (`/studies/new` and `/studies/[id]/edit`)

A multi-step form, but rendered as a single scrollable page (no wizard — researchers want to see everything). Steps are visual sections, not gated.

**Sections (in order):**

1. **Basics** — name input, research prompt textarea (with character count, min 50 chars), tags
2. **Participants** — list builder for expected participants (display name only; emails sent separately). Max 5. Drag to reorder.
3. **Moderator persona** — `<PersonaPicker>` component:
   - Grid of 6 persona cards (curated voice library)
   - Each card: voice waveform avatar, name ("Dr. Chen", "Sam", "Jordan", etc.), style tags (formal/neutral/casual, warm/clinical, slow/normal/brisk), play button to preview 8-second sample
   - Selected card has 2px info-color border (the one exception to 0.5px rule)
4. **Rules** — `<RuleConfigEditor>`:
   - List of 7 v1 rules from the engine (silence_gap, speaker_imbalance, topic_drift, cross_talk_pattern, unheard_participant, stalled_thread, time_remaining_pressure)
   - Each rule is an expandable row with:
     - Toggle (enabled), rule name, one-line description, current threshold summary
     - Expanded: parameter sliders/inputs, cooldown duration, priority, help text explaining when it fires
   - At the top: "Quietness budget" panel — two inputs (max utterances per 10min, min seconds between utterances) plus a live preview chip ("at this setting, expect ~3 interventions per hour")
5. **Recording & retention** — toggles for full mixed recording, per-participant tracks, transcript-only mode; retention days slider (1–365); IRB consent flow toggle
6. **Review & save** — summary card showing all settings, "Save as draft" + "Save and schedule session" buttons

**Form behavior:**
- Autosave drafts to localStorage every 3 seconds
- Validation inline with `react-hook-form` + zod
- Unsaved-changes warning on navigation

---

## 8. Surface 3 — Live Control (`/sessions/[id]/live`)

The most important screen. Density and clarity matter more than whitespace here.

**Layout (no sidebar on this view — full width):**

```
┌─ Top bar (48px) ────────────────────────────────────────────────┐
│ ● Live   Remote work productivity — Study #4   23:41 / 60:00    │
│                                  [Flag moment] [End session]    │
├─ Participant tiles row (h ~110px) ──────────────────────────────┤
│ [Maya] [Devon] [Priya] [Alex] [Sam]                             │
├─ Main split (flex-1) ───────────────────────────────────────────┤
│ ┌─────────────────────────┬───────────────────────────────────┐ │
│ │ Live transcript          │ Decision log                      │ │
│ │ (scrolling, auto-stick)  │ (latest at top)                   │ │
│ │                          │                                   │ │
│ │                          ├───────────────────────────────────┤ │
│ │                          │ Why quiet now?                    │ │
│ │                          │ (live rule status)                │ │
│ └─────────────────────────┴───────────────────────────────────┘ │
├─ Control bar (sticky bottom, h ~68px) ──────────────────────────┤
│ [Prompt] [Redirect] [Whisper]  | Quietness ●━━━ | Mute | Pause │
└─────────────────────────────────────────────────────────────────┘
```

**Top bar specifics:**
- "Live" pill: red bg, red text, pulsing dot (CSS animation, `prefers-reduced-motion` respected)
- Elapsed/total time in mono, tertiary color
- "End session" button has danger styling (red border, red text on hover)

**ParticipantTile component:**
- ~140px wide, fixed height
- Top row: 24px circle avatar with initials in the participant's ramp color, name, speaking indicator (animated green dot when active) or flag pill (amber for dominating/silent)
- Middle: share % with fair share as ghost text, e.g. "18% / 20% fair"; share % goes amber when flag-worthy
- Bottom: "Last spoke: now / 0:32 / 4:12" — amber if > 3min
- Compact, no border between rows

**Live transcript:**
- White card, fills available height, scrollable
- Each utterance: participant name (in their ramp color), timestamp (mono, tertiary), text
- Auto-scroll to bottom unless user has scrolled up (then show "↓ jump to live" floating button)
- Words flagged by topic-drift detection get a subtle warning-bg highlight
- VAD-active participants show a "typing"-style three-dot indicator while transcription lags

**Decision log:**
- Newest at top, scrollable
- Each entry: timestamp (mono), action verb ("Silent" tertiary, "Prompted Priya" info, "Redirected" purple, "Suggested turns" warning), rule name (small, tertiary), and for non-silent: the spoken line in italic with a left-border accent in the action's color
- Silent-only entries collapse into a single "Silent · 14 ticks" row when consecutive (click to expand)

**Why quiet now? panel:**
- Updates every tick (~500ms in mock)
- List of all active rules with status: armed (filled dot in warning color, "X / Y threshold"), cooling (tertiary dot, "cooldown Ns"), idle (tertiary dot, predicate not met)
- Progress bars on "armed" rules showing fill toward firing threshold — this is what makes the system feel alive when silent

**Control bar:**
- Sticky to viewport bottom, white bg, top border
- Left: three action buttons — Prompt, Redirect, Whisper (each opens `<InterventionModal>`)
- Center: quietness slider (1–10), label "Quiet" left / "Chatty" right
- Right: Mute toggle (icon changes), Pause toggle
- Keyboard shortcuts: P (prompt), R (redirect), W (whisper), F (flag), M (mute), space (pause). Show shortcut hints in button tooltips.

**InterventionModal:**
- Triggered by Prompt/Redirect/Whisper buttons
- Modal with: target participant dropdown (if applicable), free-text "hint" textarea, "Preview phrasing" button that calls a mock LLM and shows the generated sentence, "Send" button
- Preview shows the generated line in italic with the moderator persona's voice icon — researcher confirms before it goes out
- Whisper modal is different: no preview, just verbatim text input → speaks immediately

**Live data simulation:**
- `mock-realtime.ts` exports a `MockRealtimeProvider` React context
- Internally runs a tick every 500ms, advancing session state, occasionally appending utterances, occasionally firing rule evaluations
- Decisions appear on a believable cadence (most ticks silent, ~3 spoken interventions across a 5-min watch)
- Speaking participant cycles realistically (Devon dominant, Priya quiet, others moderate)

---

## 9. Surface 4 — Replay & Analysis (`/sessions/[id]/replay`)

Visually distinct from Live (no red indicator, slightly cooler background tint `--bg-tertiary` for the page, archive icon in top bar).

**Layout:**

```
┌─ Top bar ───────────────────────────────────────────────────────┐
│ ⟲ Replay   Remote work productivity — Study #4   Mar 12 · 58:23│
│                                          [Filter] [Export]     │
├─ Timeline panel (h ~280px) ─────────────────────────────────────┤
│ ▶ 23:41 / 58:23  ━━━━●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 1x        │
│                                                                 │
│  Maya   ▓▓ ▓▓▓▓  ▓▓  ▓▓▓▓▓▓▓ ▓▓▓ ▓ ▓▓▓▓▓ ▓▓ ▓▓▓▓ ▓▓ ▓▓▓ ▓▓▓▓  │
│  Devon  ▓▓▓▓▓▓ ▓▓▓▓ ▓▓▓▓▓▓▓ ▓▓▓▓▓ ▓▓▓▓ ▓▓▓▓▓ ▓▓▓ ▓▓ ▓▓ ▓     │
│  Priya  ▓     ▓▓        ▓▓▓        ▓▓▓▓        ▓▓             │
│  Alex   ▓▓ ▓▓▓▓ ▓▓▓ ▓▓▓ ▓▓▓▓ ▓▓▓ ▓▓▓▓ ▓▓ ▓▓▓ ▓▓▓ ▓▓▓ ▓▓▓▓▓   │
│  Sam    ▓▓ ▓▓▓     ▓▓     ▓▓▓     ▓▓▓     ▓▓     ▓▓▓▓        │
│         ─────────────────────────────────────────────────       │
│         ○  ○   ◇   ○    △       ●  ◇    ▼                     │
│         legend: ○ prompt  ◇ redirect  △ turns  ● researcher  ▼flag│
├─ Bottom split (flex-1) ─────────────────────────────────────────┤
│ ┌─────────────────────────┬───────────────────────────────────┐ │
│ │ State at 23:41           │ Decision at 23:41                 │ │
│ │ (participant table)      │ "Priya, you were nodding..."     │ │
│ │                          │ Rules evaluated (all of them)     │ │
│ └─────────────────────────┴───────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

**Timeline component (`<Timeline>`):**
- Render as a single SVG, 660px+ wide, responsive
- 5 horizontal participant lanes (14px tall each, 4px gap), each filled with speech segments in that participant's ramp color
- Below lanes: decision markers as colored circles/diamonds/triangles, hover shows tooltip with action + reason
- Scrubber: thin horizontal track, draggable thumb (12px circle), click anywhere to seek
- Vertical "now" line (dashed, 40% opacity) spans full timeline at scrubber position
- Tick marks every 15min, mono labels
- Scrub action updates state/decision panels in real-time
- Audio play button (play/pause toggle) + speed selector (0.5x / 1x / 1.5x / 2x)

**State snapshot panel:**
- Table identical in structure to ParticipantTile data but more detail
- Shows the exact state at the scrubbed moment
- "tick #2843" mono identifier in the header — researchers cite this in notes

**Decision detail panel:**
- Action pill at top (info color for prompt, purple for redirect, etc.) with target name if applicable
- Quoted spoken line in italic with left-border accent in action color (skip if `action === 'stay_silent'`)
- "Reason: " human-readable explanation
- "Reason codes: " comma-separated structured codes in mono
- **Rules evaluated section** — the critical piece. List every rule with:
  - Check icon (green) for fired
  - Minus icon (tertiary) for not fired
  - Rule name, then status text: "fired · 0.87" (confidence) or "cooldown 47s" or "not armed" or "similarity 0.71" (predicate value)
  - This is where researchers verify the engine reasoned correctly

**Filters (top bar):**
- Slide-over panel with: action type checkboxes, participant checkboxes, rule name multi-select, source toggle (auto/manual/all), flagged-only toggle
- Filtering hides non-matching markers on the timeline but keeps lanes intact

**Export (top bar):**
- Modal with checkboxes for: transcript (.txt and .vtt), decision log (.csv), state snapshots (.jsonl), audio recording (.mp3), flagged moments only (sub-option)
- "Generate export" button → mock progress bar → download triggers

---

## 10. Auxiliary surfaces

**Auth (`/login`):**
- Centered card on `--bg-secondary` page bg
- Verbio wordmark, "Sign in to your account" h2, email input, "Send magic link" button
- After submit: "Check your email" success state

**Participant pre-join (`/join/[token]`):**
- Public, no auth
- Welcome message with study name (no internal details)
- Mic and camera test (mock — show device permission UI)
- Consent checkboxes (IRB-style: recording, data use, withdrawal rights)
- Display name input (prefilled if researcher set one)
- "Join session" button → goes to a "waiting for moderator to start" screen with a calming animation

**Settings (`/settings`):**
- Tabs: Profile, Organization, Integrations (LiveKit, Deepgram, Anthropic, Cartesia keys — masked), Billing (placeholder), API keys
- Standard form patterns

---

## 11. Components to build (priority order)

1. App shell + sidebar + top bar
2. Mock data layer + realtime simulator
3. Study list + study setup form
4. ParticipantTile, DecisionLog, WhyQuietPanel, ControlBar
5. Live Control page wiring all four together with realtime hook
6. InterventionModal with mock LLM preview
7. Timeline (SVG-driven, this is the highest-effort component)
8. State snapshot + decision detail panels
9. Replay page wiring
10. Filters, Export modal
11. Auth, participant join, settings

---

## 12. Accessibility

This is non-negotiable.

- Every interactive element keyboard-navigable; focus rings visible (2px info-color outline-offset 2)
- All buttons have accessible labels; icon-only buttons get `aria-label`
- Live regions: decision log uses `aria-live="polite"`, "Live" pill uses `aria-live="off"` (decorative)
- Color is never the only signal — flags use both color and text labels
- Timeline keyboard-controllable: arrow keys seek ±5s, shift+arrow ±30s, space play/pause
- `prefers-reduced-motion`: no pulsing dot, no waveform animation, no auto-scroll smoothing
- Color contrast ≥ 4.5:1 for all text against its background; verify the warning-amber-on-white case explicitly

---

## 13. What to ship

Build the complete dashboard with all surfaces above wired to realistic mock data. The user should be able to:

1. Land on `/studies`, browse studies, click into one
2. Create a new study end-to-end, save it
3. Click "Start session" on a study → land on Live Control with simulated live activity (transcript flowing, participants speaking, rules evaluating, occasional decisions)
4. Interact: open Prompt modal, see preview, send. Watch it appear in the decision log. Mute the moderator. Drag the quietness slider.
5. End the session → land on Replay for the same session
6. Scrub the timeline, click decision markers, watch state and rule evaluation panels update
7. Filter decisions, open export modal

The build is "done" when a researcher can do the full flow without seeing a single placeholder, the dark mode looks intentional (not just inverted), and the keyboard shortcuts all work.

---

## 14. Ship checklist before declaring complete

- [ ] All TypeScript passes strict mode, no `any` without justification
- [ ] No console warnings or errors in any flow
- [ ] Dark mode tested on every page (the page bg shifts, all text remains readable)
- [ ] Keyboard navigation works end-to-end without a mouse
- [ ] Mock realtime simulator runs for 10 minutes without stuttering or memory leak
- [ ] Timeline renders correctly with 60-minute and 5-minute sessions (extremes)
- [ ] Empty states for every list and table
- [ ] Loading skeletons match final layout exactly (no layout shift)
- [ ] All forms have inline validation, autosave where appropriate, and unsaved-changes guards
- [ ] README with run instructions and a 60-second tour of what's in the build

Build it like you'd be proud to put it in your portfolio. This is the standard.
