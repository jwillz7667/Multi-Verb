// ─────────────────────────────────────────────────────────────
// Realtime simulator for Live Control.
// useSessionSim returns { elapsed, utterances, decisions, ruleStatus, participantState }
// and exposes addManualDecision to inject researcher actions.
// ─────────────────────────────────────────────────────────────

function useSessionSim({ simSpeed = 1, paused = false, muted = false, quietness = 5 }) {
  const [elapsed, setElapsed] = useState(1421); // seconds since session start
  const [utterances, setUtterances] = useState(() => MockData.initialUtterances.slice());
  const [decisions, setDecisions] = useState(() => MockData.decisions.slice());
  const [pState, setPState] = useState(() => ({ ...MockData.INITIAL_STATE }));
  const [ruleStatus, setRuleStatus] = useState(() => ({
    silence_gap:           { status: 'idle',     progress: 0.15, label: 'silence 1.2s'           },
    speaker_imbalance:     { status: 'armed',    progress: 0.72, label: 'Devon 41% vs 20% fair' },
    topic_drift:           { status: 'idle',     progress: 0.18, label: 'similarity 0.71'         },
    cross_talk_pattern:    { status: 'idle',     progress: 0.30, label: '1 / 3 interrupts'        },
    unheard_participant:   { status: 'armed',    progress: 0.86, label: 'Priya silent 4m12s'      },
    stalled_thread:        { status: 'idle',     progress: 0.33, label: '2 / 6 turns same dyad'   },
    time_remaining_pressure:{ status: 'cooling', progress: 0.12, label: 't-remaining 36m'        },
  }));
  const [currentSpeaker, setCurrentSpeaker] = useState('p_devon');
  const [vadParticipants, setVadParticipants] = useState(new Set(['p_devon']));

  const poolIdx = useRef(0);
  const tickRef = useRef(0);

  useEffect(() => {
    if (paused) return;
    const intervalMs = 500 / simSpeed;
    const id = setInterval(() => {
      tickRef.current += 1;
      const tick = tickRef.current;

      setElapsed((e) => e + 0.5);

      // every ~10 ticks (5s) add a new utterance
      if (tick % 10 === 0) {
        const pool = MockData.simPool;
        const next = pool[poolIdx.current % pool.length];
        poolIdx.current += 1;
        const newUtt = {
          id: 'u_live_' + tick,
          sessionId: 's_001',
          participantId: next.who,
          startTs: new Date().toISOString(),
          endTs: new Date().toISOString(),
          text: next.text,
          confidence: 0.93,
          isFinal: true,
          sessionElapsedSec: 0,
        };
        setUtterances((u) => [...u, newUtt]);
        setCurrentSpeaker(next.who);
        setVadParticipants(new Set([next.who]));

        // update participant state
        setPState((s) => {
          const ns = { ...s };
          Object.keys(ns).forEach((pid) => {
            ns[pid] = { ...ns[pid], isCurrentlySpeaking: pid === next.who, vadActive: pid === next.who };
          });
          ns[next.who] = {
            ...ns[next.who],
            speakingTimeTotalSec: ns[next.who].speakingTimeTotalSec + 4,
            speakingTimeLast5MinSec: ns[next.who].speakingTimeLast5MinSec + 4,
            speakingTimeLast60SecSec: Math.min(60, ns[next.who].speakingTimeLast60SecSec + 4),
            turnCount: ns[next.who].turnCount + 1,
            lastSpokeAt: 'now',
          };
          // recompute actual share
          const total5 = Object.values(ns).reduce((sum, p) => sum + p.speakingTimeLast5MinSec, 0) || 1;
          Object.keys(ns).forEach((pid) => {
            ns[pid].actualShareLast5MinPct = Math.round((ns[pid].speakingTimeLast5MinSec / total5) * 100);
            // flags
            const flags = [];
            if (ns[pid].actualShareLast5MinPct > 30) flags.push('dominating');
            if (ns[pid].actualShareLast5MinPct < 10 && pid !== next.who) flags.push('silent_too_long');
            ns[pid].flags = flags;
          });
          return ns;
        });
      }

      // every ~30 ticks (15s) advance rule status
      if (tick % 6 === 0) {
        setRuleStatus((rs) => {
          const ns = { ...rs };
          // gentle wander
          Object.keys(ns).forEach((k) => {
            const r = ns[k];
            let p = r.progress + (Math.random() - 0.4) * 0.08;
            p = Math.max(0, Math.min(1, p));
            let st = r.status;
            if (p > 0.85) st = 'armed';
            else if (p < 0.25) st = 'idle';
            if (st === 'cooling' && p < 0.2) st = 'idle';
            ns[k] = { ...r, progress: p, status: st };
          });
          return ns;
        });
      }

      // occasionally fire a decision (~every 90s in real time => 180 ticks)
      // quietness slider: higher = more interventions
      const baseFreq = 180;
      const adjustedFreq = Math.max(60, baseFreq * (10 - quietness) / 5);
      if (tick > 30 && tick % Math.floor(adjustedFreq) === 0) {
        const armed = Object.entries(ruleStatus).filter(([_, r]) => r.status === 'armed').map(([k]) => k);
        const ruleName = armed.length ? armed[0] : 'speaker_imbalance';
        const action = ({
          speaker_imbalance: 'suggest_turn_taking',
          topic_drift: 'redirect_topic',
          unheard_participant: 'prompt_participant',
          stalled_thread: 'summarize_thread',
          silence_gap: 'prompt_participant',
          cross_talk_pattern: 'suggest_turn_taking',
          time_remaining_pressure: 'summarize_thread',
        })[ruleName];

        const targetMap = {
          speaker_imbalance: 'p_priya',
          unheard_participant: 'p_priya',
          prompt_participant: 'p_priya',
        };
        const target = targetMap[ruleName] || null;
        const phrasings = {
          suggest_turn_taking: "Let's make sure everyone gets a chance — Priya, anything you want to add before we move on?",
          redirect_topic: "Stepping back to the original question — when assistants are wrong, what do you actually do?",
          prompt_participant: "Priya, you've been quiet — anything resonating with what's been said?",
          summarize_thread: "Quick summary of where we are: trust drops when the model sounds confident on things it doesn't know.",
        };

        const newDecision = {
          id: 'd_live_' + tick,
          sessionId: 's_001', tickId: 3500 + tick,
          timestamp: new Date().toISOString(),
          sessionElapsedSec: Math.floor(elapsed),
          action,
          targetParticipantId: target,
          source: 'auto',
          triggeringRule: ruleName,
          researcherId: null, researcherHint: null,
          reasonCodes: [ruleName + '_armed', 'cooldown_elapsed'],
          reasonHuman: `${ruleName.replace(/_/g, ' ')} threshold crossed at tick ${3500 + tick}.`,
          confidence: 0.78 + Math.random() * 0.15,
          suppressedBy: [],
          wasExecuted: !muted,
          llmOutput: muted ? null : phrasings[action],
          spokenAt: muted ? null : new Date().toISOString(),
          cooldownUntil: new Date().toISOString(),
        };
        setDecisions((d) => [newDecision, ...d]);
      }

    }, intervalMs);
    return () => clearInterval(id);
  }, [paused, simSpeed, muted, quietness]); // eslint-disable-line

  const addManualDecision = useCallback((partial) => {
    const tick = tickRef.current;
    const d = {
      id: 'd_manual_' + tick,
      sessionId: 's_001', tickId: 3500 + tick,
      timestamp: new Date().toISOString(),
      sessionElapsedSec: Math.floor(elapsed),
      action: 'prompt_participant',
      targetParticipantId: null,
      source: 'researcher_manual',
      triggeringRule: null,
      researcherId: 'r_lex', researcherHint: null,
      reasonCodes: ['researcher_manual'],
      reasonHuman: 'Researcher requested intervention.',
      confidence: 1.0,
      suppressedBy: [],
      wasExecuted: true,
      llmOutput: null,
      spokenAt: new Date().toISOString(),
      cooldownUntil: new Date().toISOString(),
      ...partial,
    };
    setDecisions((cur) => [d, ...cur]);
  }, [elapsed]);

  return { elapsed, utterances, decisions, ruleStatus, pState, currentSpeaker, vadParticipants, addManualDecision };
}

window.useSessionSim = useSessionSim;
