// ─────────────────────────────────────────────────────────────
// Verbio mock data layer
// Realistic fixtures for a 5-person focus group on AI assistants at work.
// ─────────────────────────────────────────────────────────────

(function () {
  const RAMP_COLORS = ['blue', 'amber', 'pink', 'green', 'teal'];

  // ─────────── participants ───────────
  const PARTICIPANTS = [
    { id: 'p_maya',   displayName: 'Maya Ellingsworth',  initials: 'ME', colorRamp: 'blue',  joinedAt: '2026-05-12T17:00:14Z', leftAt: null },
    { id: 'p_devon',  displayName: 'Devon Park',         initials: 'DP', colorRamp: 'amber', joinedAt: '2026-05-12T17:00:21Z', leftAt: null },
    { id: 'p_priya',  displayName: 'Priya Subramanian',  initials: 'PS', colorRamp: 'pink',  joinedAt: '2026-05-12T17:01:02Z', leftAt: null },
    { id: 'p_alex',   displayName: 'Alex Tanaka',        initials: 'AT', colorRamp: 'green', joinedAt: '2026-05-12T17:00:39Z', leftAt: null },
    { id: 'p_sam',    displayName: 'Sam Boruch',         initials: 'SB', colorRamp: 'teal',  joinedAt: '2026-05-12T17:00:48Z', leftAt: null },
  ];

  // initial participant state — Devon dominant, Priya quiet
  const INITIAL_STATE = {
    p_maya:  { speakingTimeTotalSec: 312, speakingTimeLast5MinSec: 78, speakingTimeLast60SecSec: 8,  turnCount: 14, lastSpokeAt: 'now-22s',   isCurrentlySpeaking: false, vadActive: false, backchannelCountLast2Min: 4, interruptionCount: 1, wasInterruptedCount: 2, fairSharePct: 20, actualShareLast5MinPct: 22, flags: [] },
    p_devon: { speakingTimeTotalSec: 612, speakingTimeLast5MinSec: 168,speakingTimeLast60SecSec: 38, turnCount: 23, lastSpokeAt: 'now-2s',    isCurrentlySpeaking: true,  vadActive: true,  backchannelCountLast2Min: 1, interruptionCount: 5, wasInterruptedCount: 0, fairSharePct: 20, actualShareLast5MinPct: 41, flags: ['dominating'] },
    p_priya: { speakingTimeTotalSec: 84,  speakingTimeLast5MinSec: 11, speakingTimeLast60SecSec: 0,  turnCount: 4,  lastSpokeAt: 'now-4m12s', isCurrentlySpeaking: false, vadActive: false, backchannelCountLast2Min: 6, interruptionCount: 0, wasInterruptedCount: 3, fairSharePct: 20, actualShareLast5MinPct: 6,  flags: ['silent_too_long'] },
    p_alex:  { speakingTimeTotalSec: 251, speakingTimeLast5MinSec: 62, speakingTimeLast60SecSec: 0,  turnCount: 12, lastSpokeAt: 'now-1m08s', isCurrentlySpeaking: false, vadActive: false, backchannelCountLast2Min: 3, interruptionCount: 2, wasInterruptedCount: 1, fairSharePct: 20, actualShareLast5MinPct: 17, flags: [] },
    p_sam:   { speakingTimeTotalSec: 219, speakingTimeLast5MinSec: 51, speakingTimeLast60SecSec: 0,  turnCount: 10, lastSpokeAt: 'now-0m48s', isCurrentlySpeaking: false, vadActive: false, backchannelCountLast2Min: 2, interruptionCount: 1, wasInterruptedCount: 2, fairSharePct: 20, actualShareLast5MinPct: 14, flags: [] },
  };

  // ─────────── transcript: seed utterances ───────────
  // Topic: how AI assistants are changing day-to-day knowledge work
  const SEED_UTTERANCES = [
    { who: 'p_devon', t: -1340, text: "The biggest shift for me is that I stopped writing first drafts. I just outline what I want and the assistant fills in the boilerplate." },
    { who: 'p_maya',  t: -1295, text: "I do the same for design specs. But I rewrite about half of what comes back — the voice is always slightly off." },
    { who: 'p_alex',  t: -1252, text: "Same with code review comments. It's faster, but I notice my own reasoning gets lazier when I lean on it too much." },
    { who: 'p_sam',   t: -1198, text: "That's the part I worry about. We're outsourcing the thinking, not just the typing." },
    { who: 'p_devon', t: -1142, text: "I'd push back on that. The thinking is in deciding what to ask for. The typing is just labor." },
    { who: 'p_maya',  t: -1086, text: "But asking the right question requires having done the typing at some point. Otherwise you don't know what to ask." },
    { who: 'p_devon', t: -1042, text: "Maybe for junior people. Once you have the pattern matching, the assistant is just velocity." },
    { who: 'p_alex',  t: -984,  text: "I'd be careful with that framing — my newer reports use it constantly and I worry they're skipping the part where the pattern forms." },
    { who: 'p_sam',   t: -922,  text: "Right. The pattern only forms by doing the thing badly a few times." },
    { who: 'p_devon', t: -866,  text: "Counterpoint: nobody benefits from making the same beginner mistakes. The tool teaches them faster than I ever could." },
    { who: 'p_maya',  t: -812,  text: "Does it though? It teaches them the answer. Not why the answer is right." },
    { who: 'p_devon', t: -758,  text: "I ask follow-ups. I push back. I treat it like a junior who needs supervision. That's where the skill is now." },
    { who: 'p_alex',  t: -698,  text: "Which assumes you're already senior enough to know when to push back. The asymmetry there bothers me." },
    { who: 'p_devon', t: -642,  text: "Sure but the same thing was true of Stack Overflow ten years ago and we got over it." },
    { who: 'p_sam',   t: -584,  text: "Stack Overflow had a community vouching for answers. The assistant has no skin in the game when it's wrong." },
    { who: 'p_maya',  t: -528,  text: "And it's confidently wrong in a way that's hard to notice if you're not already an expert." },
    { who: 'p_devon', t: -468,  text: "I just don't see the harm at scale. Output is up, people are happier, the work ships." },
    { who: 'p_alex',  t: -412,  text: "Output is up if you measure output. We're not measuring what gets lost." },
    { who: 'p_sam',   t: -362,  text: "There's also a flattening of voice. Everything starts to read the same way." },
    { who: 'p_maya',  t: -308,  text: "I notice that in copy from across our org now. It used to feel like different people. Now it all feels like one slightly-too-polished voice." },
    { who: 'p_devon', t: -252,  text: "That's an aesthetic complaint though, not a productivity one." },
    { who: 'p_alex',  t: -202,  text: "I think the aesthetic complaint is downstream of a trust complaint. If everything sounds the same I can't tell whose judgment I'm reading." },
    { who: 'p_sam',   t: -158,  text: "And judgment is exactly what we say we want to preserve. So losing the signal of who's writing matters." },
    { who: 'p_devon', t: -108,  text: "Fine, but the fix is to write more carefully when it matters and let the assistant handle the rest. Not to stop using it." },
    { who: 'p_maya',  t: -64,   text: "The trouble is the boundary between 'matters' and 'doesn't matter' moves. I keep finding the assistant snuck into things I thought I was writing carefully." },
    { who: 'p_alex',  t: -34,   text: "That's the thing for me. The tool doesn't respect the boundary I thought I'd set with myself." },
    { who: 'p_sam',   t: -12,   text: "And once you notice that, you start to wonder which parts of your own writing are actually yours anymore." },
    { who: 'p_devon', t: -2,    text: "Look, I think we're catastrophizing. I use it every day and I still feel like me at the end of it." },
  ];

  // continuing pool for live sim
  const SIM_POOL = [
    { who: 'p_maya',  text: "I want to come back to what Alex said about juniors. The asymmetry is real and we're not designing around it." },
    { who: 'p_priya', text: "Can I jump in? I've been quiet but I think the asymmetry isn't only juniors. Anyone outside their domain is a junior the moment they ask." },
    { who: 'p_alex',  text: "Yes — that's exactly the framing. The tool turns everyone into a slightly-overconfident generalist." },
    { who: 'p_devon', text: "I'd still rather be a confident generalist than a paralyzed specialist." },
    { who: 'p_sam',   text: "Those aren't the only two options though." },
    { who: 'p_maya',  text: "What about the case where you genuinely need expert review? The assistant tells you something plausible, you ship it, and you find out three weeks later it was wrong." },
    { who: 'p_priya', text: "That happened to me last month. I corrected a contract clause based on what the assistant suggested. Legal flagged it the next week." },
    { who: 'p_devon', text: "But you caught it. The system worked." },
    { who: 'p_priya', text: "We caught it because legal reads everything. Not because I would have noticed." },
    { who: 'p_alex',  text: "That's the part. The catching is happening downstream and we're not always going to have downstream." },
    { who: 'p_sam',   text: "It's also worth saying — the assistant doesn't tell you it doesn't know. It tells you the most likely answer with the same confidence as the certain ones." },
    { who: 'p_maya',  text: "Which would be fine if we were trained to interrogate confident-sounding text. We're trained to trust it." },
    { who: 'p_devon', text: "I think we're going to disagree on the trust question. I've gotten faster at sniffing out hallucinations." },
    { who: 'p_priya', text: "That's a real skill. But it's a skill the tool is creating demand for, not solving." },
    { who: 'p_alex',  text: "Right — there's a meta-cost no one accounts for. The vigilance tax." },
    { who: 'p_sam',   text: "The vigilance tax. I'm going to steal that." },
    { who: 'p_maya',  text: "So what would help? Better confidence calibration? UI that flags uncertain claims?" },
    { who: 'p_priya', text: "Honestly, citations. If it could tell me where the claim came from I'd trust the parts I could check." },
    { who: 'p_devon', text: "Citations slow it down. Half the value is the speed." },
    { who: 'p_alex',  text: "We can have both. We have both in research tools. It's a choice." },
  ];

  // generate transcript with proper timestamps. ts is "minutes:seconds" from start
  const STUDY_START = new Date('2026-05-12T17:00:00Z').getTime();
  function makeUtterance(idx, who, text, sessionElapsedSec) {
    return {
      id: 'u_' + idx,
      sessionId: 's_001',
      participantId: who,
      startTs: new Date(STUDY_START + sessionElapsedSec * 1000).toISOString(),
      endTs:   new Date(STUDY_START + (sessionElapsedSec + 5) * 1000).toISOString(),
      sessionElapsedSec,
      text,
      confidence: 0.94,
      isFinal: true,
    };
  }

  // seed: spread across first 23 minutes
  const initialUtterances = [];
  let baseTime = 60; // start at 1 minute in
  SEED_UTTERANCES.forEach((u, i) => {
    const elapsed = baseTime + i * 50 + Math.floor(Math.random() * 15);
    initialUtterances.push(makeUtterance(i, u.who, u.text, elapsed));
  });

  // ─────────── decisions / rules ───────────
  const RULES = [
    { name: 'silence_gap',           desc: 'No speech for N seconds while topic incomplete', threshold: 'silence > 8s', cooldownSec: 45, priority: 3 },
    { name: 'speaker_imbalance',     desc: 'One participant exceeds fair share by Δ%',       threshold: 'Δ > 15%',      cooldownSec: 90, priority: 5 },
    { name: 'topic_drift',           desc: 'Semantic similarity to study prompt drops',      threshold: 'sim < 0.40',   cooldownSec: 120,priority: 4 },
    { name: 'cross_talk_pattern',    desc: 'Repeated interruptions within window',           threshold: 'interrupts ≥ 3',cooldownSec: 60,priority: 6 },
    { name: 'unheard_participant',   desc: 'Participant silent ≥ N min while VAD shows interest', threshold: 'silent > 4m', cooldownSec: 180, priority: 7 },
    { name: 'stalled_thread',        desc: 'Same participants exchanging without progress',  threshold: 'turns > 6 same dyad', cooldownSec: 90, priority: 2 },
    { name: 'time_remaining_pressure', desc: 'Approaching end with uncovered topics',        threshold: 't_remaining < 10m', cooldownSec: 300, priority: 8 },
  ];

  const DECISIONS_SEED = [
    {
      id: 'd_001', sessionId: 's_001', tickId: 1240,
      timestamp: new Date(STUDY_START + 487 * 1000).toISOString(),
      sessionElapsedSec: 487,
      action: 'suggest_turn_taking',
      targetParticipantId: null,
      source: 'auto',
      triggeringRule: 'speaker_imbalance',
      researcherId: null, researcherHint: null,
      reasonCodes: ['imbalance_devon_38pct', 'fair_share_breach_18pct'],
      reasonHuman: "Devon is at 38% share of speaking time in the last 5 minutes; fair share is 20%.",
      confidence: 0.82,
      suppressedBy: [],
      wasExecuted: true,
      llmOutput: "Let's hear from someone we haven't heard from in a bit — Priya, was there anything in what Devon just said you'd push back on?",
      spokenAt: new Date(STUDY_START + 489 * 1000).toISOString(),
      cooldownUntil: new Date(STUDY_START + 577 * 1000).toISOString(),
    },
    {
      id: 'd_002', sessionId: 's_001', tickId: 2104,
      timestamp: new Date(STUDY_START + 832 * 1000).toISOString(),
      sessionElapsedSec: 832,
      action: 'redirect_topic',
      targetParticipantId: null,
      source: 'auto',
      triggeringRule: 'topic_drift',
      researcherId: null, researcherHint: null,
      reasonCodes: ['sim_to_prompt_0.31', 'drift_topic_stackoverflow'],
      reasonHuman: "Conversation about Stack Overflow drifted from the study prompt about AI assistants in work; semantic similarity dropped to 0.31.",
      confidence: 0.74,
      suppressedBy: [],
      wasExecuted: true,
      llmOutput: "Bringing us back to the original question — when you use AI assistants at work, what specifically about the experience shapes your trust in the output?",
      spokenAt: new Date(STUDY_START + 834 * 1000).toISOString(),
      cooldownUntil: new Date(STUDY_START + 952 * 1000).toISOString(),
    },
    {
      id: 'd_003', sessionId: 's_001', tickId: 2890,
      timestamp: new Date(STUDY_START + 1145 * 1000).toISOString(),
      sessionElapsedSec: 1145,
      action: 'prompt_participant',
      targetParticipantId: 'p_priya',
      source: 'researcher_manual',
      triggeringRule: null,
      researcherId: 'r_lex', researcherHint: "Priya was nodding when Maya mentioned voice flattening — would love her perspective.",
      reasonCodes: ['researcher_manual'],
      reasonHuman: "Researcher requested Priya address the voice-flattening point.",
      confidence: 1.0,
      suppressedBy: [],
      wasExecuted: true,
      llmOutput: "Priya, you were nodding when Maya mentioned voice flattening — what's coming up for you there?",
      spokenAt: new Date(STUDY_START + 1147 * 1000).toISOString(),
      cooldownUntil: new Date(STUDY_START + 1235 * 1000).toISOString(),
    },
    {
      id: 'd_004', sessionId: 's_001', tickId: 3120,
      timestamp: new Date(STUDY_START + 1241 * 1000).toISOString(),
      sessionElapsedSec: 1241,
      action: 'stay_silent',
      targetParticipantId: null,
      source: 'auto',
      triggeringRule: null,
      researcherId: null, researcherHint: null,
      reasonCodes: ['cooldown_active', 'natural_turn_taking'],
      reasonHuman: "Cooldown still active from manual prompt; turn-taking is healthy without intervention.",
      confidence: 0.91,
      suppressedBy: ['cooldown_manual'],
      wasExecuted: false,
      llmOutput: null,
      spokenAt: null,
      cooldownUntil: new Date(STUDY_START + 1241 * 1000).toISOString(),
    },
  ];

  // expand decision-evaluation: for any given decision, return per-rule evaluation
  function buildRuleEvalsFor(decision) {
    return RULES.map((rule) => {
      const isTrigger = decision.triggeringRule === rule.name;
      const suppressed = decision.suppressedBy.length > 0 && rule.name === 'silence_gap';
      let status = 'idle';
      let statusText = 'not armed';
      let predicateValue = 0;
      if (isTrigger) {
        status = 'fired';
        statusText = 'fired · ' + decision.confidence.toFixed(2);
      } else if (rule.name === 'speaker_imbalance') {
        predicateValue = 0.21;
        statusText = 'imbalance 21%, cooldown';
        status = 'cooling';
      } else if (rule.name === 'topic_drift') {
        statusText = 'similarity 0.71';
        status = 'idle';
      } else if (rule.name === 'silence_gap') {
        statusText = 'silence 1.2s';
        status = 'idle';
      } else if (rule.name === 'cross_talk_pattern') {
        statusText = '1 / 3 interrupts';
        status = 'idle';
      } else if (rule.name === 'unheard_participant') {
        statusText = 'Priya silent 4m12s';
        status = decision.tickId > 2500 ? 'cooling' : 'armed';
      } else if (rule.name === 'stalled_thread') {
        statusText = '2 / 6 turns';
        status = 'idle';
      } else if (rule.name === 'time_remaining_pressure') {
        statusText = 't-37m';
        status = 'idle';
      }
      return {
        id: 'r_' + decision.id + '_' + rule.name,
        decisionId: decision.id,
        ruleName: rule.name,
        ruleVersion: '1.0',
        fired: isTrigger,
        status,
        statusText,
        suppressedReason: suppressed ? 'cooldown_active' : null,
        predicateInputs: { value: predicateValue },
        confidence: isTrigger ? decision.confidence : 0,
      };
    });
  }

  // ─────────── studies ───────────
  const STUDIES = [
    {
      id: 'st_001',
      name: 'AI assistants in knowledge work',
      prompt: "Explore how mid-career knowledge workers feel about AI assistants in their daily writing, coding, and decision-making. Surface where trust forms, where it breaks, and what they would change about the tools they currently use.",
      tags: ['mixed-method', 'enterprise', 'trust'],
      status: 'active',
      sessionCount: 4,
      lastSessionDate: '2026-05-12',
      createdAt: '2026-04-21',
    },
    {
      id: 'st_002',
      name: 'Onboarding friction in B2B SaaS',
      prompt: "Understand the first 72 hours of self-serve onboarding for technical buyers evaluating data infrastructure tools. Identify points where they would have abandoned without intervention.",
      tags: ['onboarding', 'b2b'],
      status: 'active',
      sessionCount: 7,
      lastSessionDate: '2026-05-09',
      createdAt: '2026-03-15',
    },
    {
      id: 'st_003',
      name: 'Therapy outcomes — clinician interviews',
      prompt: "Speak with licensed therapists about how they measure session outcomes when client-reported metrics are absent or unreliable. Identify the heuristics they actually use versus the ones their training prescribes.",
      tags: ['healthcare', 'clinician'],
      status: 'active',
      sessionCount: 12,
      lastSessionDate: '2026-05-11',
      createdAt: '2026-02-04',
    },
    {
      id: 'st_004',
      name: 'Pricing pages — first impressions',
      prompt: "Five-minute timeboxed reactions to enterprise pricing pages from procurement-adjacent buyers. Surface where price legibility breaks and which patterns cause buyers to seek a sales conversation.",
      tags: ['pricing', 'b2b'],
      status: 'draft',
      sessionCount: 0,
      lastSessionDate: null,
      createdAt: '2026-05-08',
    },
    {
      id: 'st_005',
      name: 'Remote performance reviews — manager perspective',
      prompt: "Talk to people-managers who have run performance reviews fully-remote for at least three cycles. Understand where remote-only review dynamics under-serve them and which rituals they invented to compensate.",
      tags: ['hr', 'remote'],
      status: 'active',
      sessionCount: 9,
      lastSessionDate: '2026-04-30',
      createdAt: '2026-01-12',
    },
    {
      id: 'st_006',
      name: 'Family meal planning — Sunday afternoon',
      prompt: "Observe how two- and three-adult households plan their week of meals on Sunday afternoon. Note where conflict emerges, who concedes, and which tools they reach for and abandon.",
      tags: ['consumer', 'household'],
      status: 'active',
      sessionCount: 5,
      lastSessionDate: '2026-05-04',
      createdAt: '2026-03-28',
    },
    {
      id: 'st_007',
      name: 'Open source maintainer burnout',
      prompt: "Hear from solo and small-team OSS maintainers about the conditions that precede them stepping back from a project. Distinguish acute triggers from chronic conditions.",
      tags: ['developer', 'community'],
      status: 'archived',
      sessionCount: 14,
      lastSessionDate: '2025-12-19',
      createdAt: '2025-10-04',
    },
    {
      id: 'st_008',
      name: 'Cooking shows — passive vs active viewing',
      prompt: "Side-by-side viewings of two cooking-show formats with the same household. Surface where attention shifts from passive entertainment to active learning, and what production choices drive that switch.",
      tags: ['media', 'household'],
      status: 'draft',
      sessionCount: 0,
      lastSessionDate: null,
      createdAt: '2026-05-13',
    },
  ];

  // ─────────── sessions ───────────
  const SESSIONS = [
    {
      id: 's_001',
      studyId: 'st_001',
      studyName: 'AI assistants in knowledge work',
      status: 'live',
      scheduledStart: '2026-05-12T17:00:00Z',
      actualStart: '2026-05-12T17:00:14Z',
      actualEnd: null,
      participants: PARTICIPANTS,
      durationSec: 1421, // current elapsed in mock
      totalDurationSec: 3600,
    },
    {
      id: 's_002',
      studyId: 'st_001',
      studyName: 'AI assistants in knowledge work',
      status: 'ended',
      scheduledStart: '2026-05-05T16:00:00Z',
      actualStart: '2026-05-05T16:00:22Z',
      actualEnd: '2026-05-05T16:58:23Z',
      participants: PARTICIPANTS,
      durationSec: 3481,
      totalDurationSec: 3481,
    },
  ];

  // build markers for replay (every decision becomes a marker)
  function buildReplayMarkers() {
    return DECISIONS_SEED.map(d => ({
      id: d.id,
      atSec: d.sessionElapsedSec,
      action: d.action,
      source: d.source,
      target: d.targetParticipantId,
      decision: d,
    }));
  }

  // build speech segments for timeline visualization (per participant lanes)
  // produce realistic segments across totalDurationSec
  function buildSpeechSegments(totalSec) {
    // patterns by participant
    // Devon: dense (40% air time), Maya: 22%, Alex: 18%, Sam: 14%, Priya: 6%
    const profiles = {
      p_devon: { density: 0.40, avgLen: 14, gap: 2 },
      p_maya:  { density: 0.22, avgLen: 10, gap: 4 },
      p_alex:  { density: 0.18, avgLen: 9,  gap: 5 },
      p_sam:   { density: 0.14, avgLen: 7,  gap: 6 },
      p_priya: { density: 0.06, avgLen: 5,  gap: 18 },
    };
    const segments = {};
    Object.keys(profiles).forEach((pid) => {
      const profile = profiles[pid];
      const arr = [];
      let t = Math.floor(Math.random() * 30);
      let seed = pid.charCodeAt(2) * 13;
      function rand() { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; }
      while (t < totalSec) {
        if (rand() < profile.density * 0.7) {
          const len = Math.max(2, Math.floor(profile.avgLen * (0.5 + rand())));
          arr.push({ start: t, end: Math.min(t + len, totalSec) });
          t += len + Math.floor(profile.gap * (0.5 + rand() * 1.5));
        } else {
          t += Math.floor(profile.gap * (0.3 + rand() * 0.6));
        }
      }
      segments[pid] = arr;
    });
    return segments;
  }

  // ─────────── persona library ───────────
  const PERSONAS = [
    { id: 'persona_chen',    name: 'Dr. Lin Chen',  styleTags: ['formal', 'warm', 'normal'],   provider: 'cartesia',  stylePrompt: 'Curious, careful, occasionally tentative. Acknowledges before redirecting.' },
    { id: 'persona_sam',     name: 'Sam',           styleTags: ['neutral', 'warm', 'normal'],  provider: 'cartesia',  stylePrompt: 'Plainspoken peer voice; never lectures.' },
    { id: 'persona_jordan',  name: 'Jordan',        styleTags: ['casual', 'warm', 'brisk'],    provider: 'elevenlabs',stylePrompt: 'Conversational, makes participants feel comfortable interrupting.' },
    { id: 'persona_rivera',  name: 'Dr. Rivera',    styleTags: ['formal', 'clinical', 'slow'], provider: 'elevenlabs',stylePrompt: 'Measured, precise, used to clinical contexts.' },
    { id: 'persona_kai',     name: 'Kai',           styleTags: ['neutral', 'clinical', 'normal'], provider: 'cartesia',stylePrompt: 'Direct but never abrupt; researcher voice.' },
    { id: 'persona_aiyana',  name: 'Aiyana',        styleTags: ['casual', 'warm', 'slow'],     provider: 'elevenlabs',stylePrompt: 'Slow, deliberate, leaves space for participants to think.' },
  ];

  // expose
  window.MockData = {
    PARTICIPANTS, INITIAL_STATE, RULES, STUDIES, SESSIONS, PERSONAS,
    initialUtterances,
    simPool: SIM_POOL,
    decisions: DECISIONS_SEED,
    buildRuleEvalsFor,
    buildReplayMarkers,
    buildSpeechSegments,
    STUDY_START,
    formatElapsed(sec) {
      const h = Math.floor(sec / 3600);
      const m = Math.floor((sec % 3600) / 60);
      const s = Math.floor(sec % 60);
      if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
      return `${m}:${String(s).padStart(2,'0')}`;
    },
    rampToVar(ramp) {
      return {
        color: `var(--ramp-${ramp})`,
        bg: `var(--ramp-${ramp}-bg)`,
      };
    },
    actionMeta(action) {
      switch (action) {
        case 'stay_silent':            return { label: 'Silent',         color: 'tertiary', verb: 'stayed silent' };
        case 'prompt_participant':     return { label: 'Prompted',       color: 'info',     verb: 'prompted' };
        case 'redirect_topic':         return { label: 'Redirected',     color: 'purple',   verb: 'redirected' };
        case 'summarize_thread':       return { label: 'Summarized',     color: 'success',  verb: 'summarized' };
        case 'request_clarification':  return { label: 'Clarified',      color: 'info',     verb: 'requested clarification' };
        case 'suggest_turn_taking':    return { label: 'Suggested turns',color: 'warning',  verb: 'suggested turn-taking' };
        case 'close_session':          return { label: 'Closed',         color: 'danger',   verb: 'closed' };
        default: return { label: action, color: 'tertiary', verb: action };
      }
    },
  };
})();
