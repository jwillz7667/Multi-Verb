// ─────────────────────────────────────────────────────────────
// Live Control page — observe and intervene during an active session.
// ─────────────────────────────────────────────────────────────

function ParticipantTile({ p, ps, isSpeaker }) {
  const overFair = ps.actualShareLast5MinPct > ps.fairSharePct + 10;
  const underFair = ps.actualShareLast5MinPct < ps.fairSharePct - 10;
  const lastSpokeText = (() => {
    if (ps.isCurrentlySpeaking) return 'speaking now';
    const s = ps.lastSpokeAt;
    if (!s || s === 'now') return 'just now';
    return s.replace('now-', '');
  })();
  return (
    <div className="live-tile" data-speaking={ps.isCurrentlySpeaking ? 'true' : 'false'}>
      <div className="tile-top">
        <ParticipantAvatar participant={p} size={26} />
        <span className="tile-name">{p.displayName.split(' ')[0]}</span>
        {ps.isCurrentlySpeaking && (
          <span className="tile-speaking">
            <span className="pulse-dot" />
            speaking
          </span>
        )}
        {!ps.isCurrentlySpeaking && ps.flags && ps.flags.includes('dominating') && (
          <span className="flag-pill dominating" style={{ marginLeft: 'auto' }}>dominating</span>
        )}
        {!ps.isCurrentlySpeaking && ps.flags && ps.flags.includes('silent_too_long') && (
          <span className="flag-pill silent" style={{ marginLeft: 'auto' }}>quiet</span>
        )}
      </div>
      <div className={`tile-share${overFair || underFair ? ' warn' : ''}`}>
        <span className="mono">{ps.actualShareLast5MinPct}%</span>
        <span className="fair">/ {ps.fairSharePct}% fair</span>
      </div>
      <div className={`tile-last${(!ps.isCurrentlySpeaking && lastSpokeText.includes('m')) ? ' warn' : ''}`}>
        Last spoke <span className="mono">{lastSpokeText}</span>
      </div>
    </div>
  );
}

function Transcript({ utterances, currentSpeaker, vadParticipants }) {
  const ref = useRef(null);
  const [showJump, setShowJump] = useState(false);
  const stuckRef = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    function onScroll() {
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      stuckRef.current = nearBottom;
      setShowJump(!nearBottom);
    }
    el.addEventListener('scroll', onScroll);
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    if (stuckRef.current && ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [utterances.length]);

  function jumpToLive() {
    if (ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
      stuckRef.current = true;
      setShowJump(false);
    }
  }

  return (
    <div className="split-pane" style={{ position: 'relative' }}>
      <div className="split-pane-header row" style={{ justifyContent: 'space-between' }}>
        <h3>Live transcript</h3>
        <span className="text-tertiary text-11 mono">
          {utterances.length} utterances · auto-scroll
        </span>
      </div>
      <div className="split-pane-body scroll" ref={ref}>
        <div className="transcript" aria-live="polite">
          {utterances.slice(-60).map((u) => {
            const p = MockData.PARTICIPANTS.find((x) => x.id === u.participantId);
            const ramp = MockData.rampToVar(p.colorRamp);
            const ts = (() => {
              const sec = Math.max(0, Math.floor((Date.now() - new Date(u.startTs).getTime()) / 1000));
              if (sec < 60) return `${sec}s ago`;
              return MockData.formatElapsed(u.sessionElapsedSec || sec);
            })();
            // highlight a couple of drifted phrases for visual interest
            const highlighted = (txt) => {
              if (/Stack Overflow|community vouching/i.test(txt)) {
                const parts = txt.split(/(Stack Overflow|community vouching)/i);
                return parts.map((p, i) => /^(Stack Overflow|community vouching)$/i.test(p) ? <mark key={i}>{p}</mark> : p);
              }
              return txt;
            };
            return (
              <div className="utt" key={u.id}>
                <span className="who" style={{ color: ramp.color }}>{p.displayName.split(' ')[0]}</span>
                <span className="ts mono">{ts}</span>
                <span className="what">{highlighted(u.text)}</span>
              </div>
            );
          })}
          {currentSpeaker && (
            <div className="utt" data-partial="true">
              <span className="who" style={{ color: MockData.rampToVar(MockData.PARTICIPANTS.find(p => p.id === currentSpeaker).colorRamp).color }}>
                {MockData.PARTICIPANTS.find(p => p.id === currentSpeaker).displayName.split(' ')[0]}
              </span>
              <span className="ts mono">now</span>
              <span className="what">
                <span className="typing"><span/><span/><span/></span>
              </span>
            </div>
          )}
        </div>
      </div>
      {showJump && (
        <button className="jump-live" onClick={jumpToLive}>
          <Icon name="arrow-down" size={12} /> Jump to live
        </button>
      )}
    </div>
  );
}

function DecisionLog({ decisions }) {
  // collapse consecutive silent auto-decisions
  const grouped = useMemo(() => {
    const out = [];
    let silentRun = null;
    decisions.forEach((d) => {
      if (d.action === 'stay_silent' && d.source === 'auto') {
        if (silentRun) {
          silentRun.count += 1;
          silentRun.end = d;
        } else {
          silentRun = { type: 'silent_run', count: 1, start: d, end: d };
        }
      } else {
        if (silentRun) { out.push(silentRun); silentRun = null; }
        out.push({ type: 'decision', d });
      }
    });
    if (silentRun) out.push(silentRun);
    return out;
  }, [decisions]);

  return (
    <div className="split-pane">
      <div className="split-pane-header row" style={{ justifyContent: 'space-between' }}>
        <h3>Decision log</h3>
        <span className="text-tertiary text-11 mono">tick {3500 + Math.floor(Math.random()*10)}</span>
      </div>
      <div className="split-pane-body scroll">
        <div className="decision-log" aria-live="polite">
          {grouped.map((g, i) => {
            if (g.type === 'silent_run' && g.count > 1) {
              return (
                <div key={i} className="decision-entry silent-collapsed">
                  <span><Icon name="dash" size={12} className="icon-dash" /> Silent · {g.count} ticks</span>
                </div>
              );
            }
            const d = g.type === 'silent_run' ? g.start : g.d;
            const meta = MockData.actionMeta(d.action);
            const target = d.targetParticipantId ? MockData.PARTICIPANTS.find(p => p.id === d.targetParticipantId) : null;
            const ts = MockData.formatElapsed(d.sessionElapsedSec || 0);
            return (
              <div key={d.id} className="decision-entry">
                <div className="decision-entry-top">
                  <span className="ts mono">{ts}</span>
                  <span className={`action action-${meta.color}`}>
                    {meta.label}{target ? ` · ${target.displayName.split(' ')[0]}` : ''}
                  </span>
                  {d.source !== 'auto' && (
                    <span className="pill" style={{ fontSize: 10, padding: '0px 6px' }}>
                      {d.source === 'researcher_manual' ? 'researcher' : 'whisper'}
                    </span>
                  )}
                  {d.triggeringRule && (
                    <span className="rule mono">{d.triggeringRule}</span>
                  )}
                </div>
                {d.llmOutput && (
                  <div className={`spoken spoken-${meta.color}`}>
                    "{d.llmOutput}"
                  </div>
                )}
                {!d.llmOutput && d.action === 'stay_silent' && (
                  <span className="text-tertiary text-12">{d.reasonHuman}</span>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function WhyQuietPanel({ ruleStatus }) {
  const rules = MockData.RULES;
  return (
    <div className="split-pane">
      <div className="split-pane-header row" style={{ justifyContent: 'space-between' }}>
        <h3>Why quiet now?</h3>
        <span className="text-tertiary text-11 mono">live · 500ms tick</span>
      </div>
      <div className="split-pane-body scroll">
        <div className="why-quiet">
          {rules.map((r) => {
            const status = ruleStatus[r.name] || { status: 'idle', progress: 0, label: 'not armed' };
            return (
              <div key={r.name} className="why-quiet-row" data-status={status.status}>
                <span className="indicator" aria-hidden="true" />
                <span className="name">{r.name}</span>
                <span className="status">{status.label}</span>
                {status.status === 'armed' && (
                  <div className="bar" style={{ gridColumn: '2 / 4', marginTop: 2 }}>
                    <div style={{ width: `${Math.round(status.progress * 100)}%` }} />
                  </div>
                )}
              </div>
            );
          })}
          <div className="divider" style={{ margin: '6px 0' }} />
          <div className="text-tertiary text-11" style={{ lineHeight: 1.45 }}>
            Moderator is reasoning continuously. It will speak when a rule fires above its priority threshold and cooldown has cleared.
          </div>
        </div>
      </div>
    </div>
  );
}

function InterventionModal({ open, mode, onClose, onSend }) {
  const [target, setTarget] = useState('p_priya');
  const [hint, setHint] = useState('');
  const [preview, setPreview] = useState(null);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    if (!open) {
      setHint(''); setPreview(null); setGenerating(false);
    }
  }, [open]);

  if (!open) return null;

  const titles = {
    prompt: 'Prompt a participant',
    redirect: 'Redirect topic',
    whisper: 'Whisper to moderator',
  };

  function generatePreview() {
    setGenerating(true);
    setTimeout(() => {
      const samples = {
        prompt: hint
          ? `${MockData.PARTICIPANTS.find(p => p.id === target).displayName.split(' ')[0]}, I want to come back to ${hint.toLowerCase().split(' ').slice(0, 5).join(' ')}. What's coming up for you there?`
          : `${MockData.PARTICIPANTS.find(p => p.id === target).displayName.split(' ')[0]}, anything resonating with what's been said?`,
        redirect: hint
          ? `Stepping back — when you think about ${hint.toLowerCase().split(' ').slice(0, 6).join(' ')}, what's the part that matters most to you?`
          : `Coming back to the original prompt — when assistants are wrong, what do you actually do about it?`,
        whisper: hint || '(Whisper has no preview — text is spoken verbatim.)',
      };
      setPreview(samples[mode]);
      setGenerating(false);
    }, 700);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={titles[mode]}
      footer={
        <>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button
            className="btn btn-primary"
            onClick={() => { onSend({ mode, target, hint, preview }); onClose(); }}
            disabled={mode !== 'whisper' && !preview}
          >
            <Icon name="send" size={13} />
            {mode === 'whisper' ? 'Speak verbatim' : 'Send to moderator'}
          </button>
        </>
      }
    >
      {mode !== 'whisper' && (
        <div className="col gap-4">
          {mode === 'prompt' && (
            <div>
              <label className="field-label" htmlFor="target">Target participant</label>
              <select id="target" className="select" value={target} onChange={(e) => setTarget(e.target.value)}>
                {MockData.PARTICIPANTS.map((p) => (
                  <option key={p.id} value={p.id}>{p.displayName}</option>
                ))}
              </select>
            </div>
          )}
          <div>
            <label className="field-label" htmlFor="hint">Hint (what should the moderator say?)</label>
            <textarea
              id="hint"
              className="textarea"
              placeholder={mode === 'prompt'
                ? "e.g. ask about the voice flattening point Maya made"
                : "e.g. bring us back to the trust question, away from Stack Overflow"}
              value={hint}
              onChange={(e) => setHint(e.target.value)}
            />
            <p className="field-help">Verbio will phrase your hint in the moderator's voice.</p>
          </div>
          <div className="row gap-2">
            <button className="btn" onClick={generatePreview} disabled={generating}>
              {generating ? 'Generating…' : 'Preview phrasing'}
            </button>
            {preview && <span className="text-tertiary text-12 mono">drafted with Dr. Lin Chen</span>}
          </div>
          {preview && (
            <div className="card" style={{ padding: 12, borderColor: 'var(--info-border)' }}>
              <div className="row gap-2 text-tertiary text-11" style={{ marginBottom: 4 }}>
                <Icon name="speaker" size={12} />
                <span>Moderator will say:</span>
              </div>
              <div style={{ fontStyle: 'italic', fontSize: 14 }}>"{preview}"</div>
            </div>
          )}
        </div>
      )}
      {mode === 'whisper' && (
        <div className="col gap-3">
          <div>
            <label className="field-label" htmlFor="verbatim">Speak this verbatim</label>
            <textarea
              id="verbatim"
              className="textarea"
              placeholder="The participant will hear exactly what you type."
              value={hint}
              onChange={(e) => setHint(e.target.value)}
            />
            <p className="field-help">No phrasing pass — your text is spoken as written.</p>
          </div>
        </div>
      )}
    </Modal>
  );
}

function ControlBar({ quietness, onQuietness, muted, onMute, paused, onPause, onOpenModal }) {
  return (
    <div className="control-bar">
      <div className="cb-actions">
        <button className="btn" onClick={() => onOpenModal('prompt')} title="Prompt (P)">
          <Icon name="prompt" size={14} /> Prompt <span className="kbd">P</span>
        </button>
        <button className="btn" onClick={() => onOpenModal('redirect')} title="Redirect (R)">
          <Icon name="redirect" size={14} /> Redirect <span className="kbd">R</span>
        </button>
        <button className="btn" onClick={() => onOpenModal('whisper')} title="Whisper (W)">
          <Icon name="whisper" size={14} /> Whisper <span className="kbd">W</span>
        </button>
      </div>
      <div className="cb-quietness">
        <span className="lbl">Quiet</span>
        <input
          type="range" min={1} max={10} step={1}
          className="slider"
          value={quietness}
          onChange={(e) => onQuietness(Number(e.target.value))}
          aria-label="Quietness budget"
        />
        <span className="lbl">Chatty</span>
        <span className="text-tertiary text-11 mono" style={{ minWidth: 18 }}>{quietness}</span>
      </div>
      <div className="cb-right">
        <button className={`btn ${muted ? 'btn-danger' : ''}`} onClick={onMute} title="Mute moderator (M)">
          <Icon name={muted ? 'mic-off' : 'mic'} size={14} /> {muted ? 'Muted' : 'Mute'} <span className="kbd">M</span>
        </button>
        <button className="btn" onClick={onPause} title="Pause session (Space)">
          <Icon name={paused ? 'play' : 'pause'} size={14} /> {paused ? 'Resume' : 'Pause'} <span className="kbd">Space</span>
        </button>
      </div>
    </div>
  );
}

function LiveControlPage({ onNavigate, theme, onToggleTheme, tweaks }) {
  const [quietness, setQuietness] = useState(tweaks.quietness ?? 5);
  const [muted, setMuted] = useState(false);
  const [paused, setPaused] = useState(false);
  const [modalMode, setModalMode] = useState(null);
  const [flagged, setFlagged] = useState(0);

  useEffect(() => { setQuietness(tweaks.quietness ?? 5); }, [tweaks.quietness]);

  const sim = useSessionSim({ simSpeed: tweaks.simSpeed, paused, muted, quietness });

  // keyboard shortcuts
  useEffect(() => {
    function onKey(e) {
      if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT')) return;
      const k = e.key.toLowerCase();
      if (k === 'p') { e.preventDefault(); setModalMode('prompt'); }
      else if (k === 'r') { e.preventDefault(); setModalMode('redirect'); }
      else if (k === 'w') { e.preventDefault(); setModalMode('whisper'); }
      else if (k === 'm') { e.preventDefault(); setMuted((m) => !m); }
      else if (k === 'f') { e.preventDefault(); setFlagged((f) => f + 1); }
      else if (e.code === 'Space') { e.preventDefault(); setPaused((p) => !p); }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  function sendIntervention({ mode, target, hint, preview }) {
    const action = mode === 'prompt' ? 'prompt_participant' : mode === 'redirect' ? 'redirect_topic' : 'prompt_participant';
    sim.addManualDecision({
      action,
      targetParticipantId: mode === 'prompt' ? target : null,
      source: mode === 'whisper' ? 'researcher_whisper' : 'researcher_manual',
      researcherHint: hint,
      llmOutput: mode === 'whisper' ? hint : preview,
    });
  }

  const totalSec = 3600;
  const remaining = totalSec - Math.floor(sim.elapsed);

  return (
    <div className="live-shell">
      <TopBar
        leadingPill={<LivePill />}
        crumbs={[
          { label: 'Studies', onClick: () => onNavigate('studies') },
          { label: 'AI assistants in knowledge work', onClick: () => onNavigate('studies') },
          { label: 'Session #4' },
        ]}
      >
        <span className="mono text-tertiary text-12">
          {MockData.formatElapsed(sim.elapsed)} / {MockData.formatElapsed(totalSec)}
        </span>
        <button className="btn" onClick={() => setFlagged((f) => f + 1)} title="Flag moment (F)">
          <Icon name="flag" size={13} /> Flag moment{flagged > 0 ? ` · ${flagged}` : ''}
        </button>
        <button className="btn btn-ghost btn-icon" aria-label="Toggle theme" onClick={onToggleTheme}>
          <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={14} />
        </button>
        <button className="btn btn-danger" onClick={() => onNavigate('replay')}>End session</button>
      </TopBar>

      <div className="live-tile-row scroll">
        {MockData.PARTICIPANTS.map((p) => (
          <ParticipantTile
            key={p.id}
            p={p}
            ps={sim.pState[p.id]}
            isSpeaker={sim.currentSpeaker === p.id}
          />
        ))}
      </div>

      <div className="split" style={{ minHeight: 0 }}>
        <Transcript
          utterances={sim.utterances}
          currentSpeaker={!paused && sim.currentSpeaker}
          vadParticipants={sim.vadParticipants}
        />
        <div className="right-col">
          <div style={{ flex: '0 0 50%', display: 'flex', minHeight: 0 }}>
            <DecisionLog decisions={sim.decisions} />
          </div>
          <div style={{ flex: '1 1 50%', display: 'flex', minHeight: 0 }}>
            <WhyQuietPanel ruleStatus={sim.ruleStatus} />
          </div>
        </div>
      </div>

      <ControlBar
        quietness={quietness}
        onQuietness={setQuietness}
        muted={muted}
        onMute={() => setMuted((m) => !m)}
        paused={paused}
        onPause={() => setPaused((p) => !p)}
        onOpenModal={setModalMode}
      />

      <InterventionModal
        open={!!modalMode}
        mode={modalMode || 'prompt'}
        onClose={() => setModalMode(null)}
        onSend={sendIntervention}
      />
    </div>
  );
}

window.LiveControlPage = LiveControlPage;
