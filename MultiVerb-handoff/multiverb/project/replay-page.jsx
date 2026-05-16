// ─────────────────────────────────────────────────────────────
// Replay & Analysis page — scrubbable timeline-driven review.
// ─────────────────────────────────────────────────────────────

function useScrubber(totalSec) {
  const [atSec, setAtSec] = useState(Math.floor(totalSec * 0.40));
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setAtSec((s) => {
        const next = s + speed * 1;
        if (next >= totalSec) { setPlaying(false); return totalSec; }
        return next;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [playing, speed, totalSec]);
  return { atSec, setAtSec, playing, setPlaying, speed, setSpeed };
}

// decision marker shape mapping
function MarkerShape({ action, source, cx, cy, r = 6, color, onHover, onLeave, onClick, selected }) {
  const stroke = selected ? 'var(--text-primary)' : 'transparent';
  const strokeWidth = selected ? 1.5 : 0;
  const common = {
    style: { cursor: 'pointer' },
    onMouseEnter: onHover, onMouseLeave: onLeave, onClick,
  };
  if (source === 'researcher_manual' || source === 'researcher_whisper') {
    return <circle cx={cx} cy={cy} r={r} fill={color} stroke={stroke} strokeWidth={strokeWidth} {...common} />;
  }
  switch (action) {
    case 'redirect_topic':
      return <rect x={cx-r} y={cy-r} width={r*2} height={r*2} fill={color} transform={`rotate(45 ${cx} ${cy})`} stroke={stroke} strokeWidth={strokeWidth} {...common}/>;
    case 'suggest_turn_taking':
      return <polygon points={`${cx},${cy-r} ${cx+r},${cy+r} ${cx-r},${cy+r}`} fill={color} stroke={stroke} strokeWidth={strokeWidth} {...common}/>;
    case 'summarize_thread':
      return <polygon points={`${cx},${cy+r} ${cx+r},${cy-r} ${cx-r},${cy-r}`} fill={color} stroke={stroke} strokeWidth={strokeWidth} {...common}/>;
    default:
      return <circle cx={cx} cy={cy} r={r-1} fill={color} stroke={stroke} strokeWidth={strokeWidth} {...common}/>;
  }
}

function Timeline({ totalSec, atSec, onSeek, markers, selectedMarker, onSelectMarker, segments }) {
  const wrapRef = useRef(null);
  const [width, setWidth] = useState(900);
  const [tooltip, setTooltip] = useState(null);
  const LANE_H = 14;
  const LANE_GAP = 4;
  const PAD_LEFT = 60;
  const PAD_RIGHT = 16;
  const lanes = MockData.PARTICIPANTS;
  const lanesTotal = lanes.length * (LANE_H + LANE_GAP);
  const markerY = 24 + lanesTotal + 14;
  const tickY = 16;
  const totalH = markerY + 22;

  useLayoutEffect(() => {
    function measure() {
      if (wrapRef.current) setWidth(wrapRef.current.clientWidth);
    }
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, []);

  const innerW = Math.max(300, width - PAD_LEFT - PAD_RIGHT);
  const xFor = (sec) => PAD_LEFT + (sec / totalSec) * innerW;
  const wFor = (sec) => (sec / totalSec) * innerW;

  function onTrackClick(e) {
    const rect = wrapRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left - PAD_LEFT;
    const ratio = Math.max(0, Math.min(1, x / innerW));
    onSeek(Math.floor(ratio * totalSec));
  }

  function onTrackMove(e) {
    if (e.buttons !== 1) return;
    onTrackClick(e);
  }

  // tick marks every 5 minutes
  const ticks = [];
  for (let t = 0; t <= totalSec; t += 300) ticks.push(t);

  return (
    <div className="timeline-svg-wrap" ref={wrapRef}>
      <svg
        height={totalH}
        viewBox={`0 0 ${width} ${totalH}`}
        onClick={onTrackClick}
        onMouseMove={onTrackMove}
        style={{ userSelect: 'none' }}
      >
        {/* tick marks */}
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={xFor(t)} y1={tickY-4} x2={xFor(t)} y2={tickY+2} stroke="var(--border-emphasis)" />
            <text x={xFor(t)} y={tickY-6} fontSize="10" fill="var(--text-tertiary)" textAnchor="middle" fontFamily="'IBM Plex Mono', monospace">
              {MockData.formatElapsed(t)}
            </text>
          </g>
        ))}

        {/* lanes: name labels + speech segments */}
        {lanes.map((p, i) => {
          const y = 24 + i * (LANE_H + LANE_GAP);
          const ramp = MockData.rampToVar(p.colorRamp);
          return (
            <g key={p.id}>
              <text x={PAD_LEFT - 8} y={y + LANE_H / 2 + 3} fontSize="10.5" textAnchor="end" fill="var(--text-secondary)">
                {p.displayName.split(' ')[0]}
              </text>
              {/* lane bg */}
              <rect x={PAD_LEFT} y={y} width={innerW} height={LANE_H} fill="var(--bg-tertiary)" rx="2"/>
              {/* segments */}
              {(segments[p.id] || []).map((s, j) => (
                <rect
                  key={j}
                  x={xFor(s.start)}
                  y={y + 1}
                  width={Math.max(1, wFor(s.end - s.start))}
                  height={LANE_H - 2}
                  fill={ramp.color}
                  opacity={0.85}
                  rx="1"
                />
              ))}
            </g>
          );
        })}

        {/* decision markers row */}
        <line x1={PAD_LEFT} y1={markerY} x2={PAD_LEFT + innerW} y2={markerY} stroke="var(--border-default)" />
        {markers.map((m) => {
          const cx = xFor(m.atSec);
          const meta = MockData.actionMeta(m.action);
          const colorMap = {
            info: 'var(--info)', warning: 'var(--warning)', purple: 'var(--accent-purple)',
            success: 'var(--success)', danger: 'var(--danger)', tertiary: 'var(--text-tertiary)',
          };
          const color = colorMap[meta.color] || 'var(--text-tertiary)';
          return (
            <MarkerShape
              key={m.id}
              action={m.action}
              source={m.source}
              cx={cx} cy={markerY}
              color={color}
              selected={selectedMarker === m.id}
              onClick={(e) => { e.stopPropagation(); onSelectMarker(m.id); onSeek(m.atSec); }}
              onHover={(e) => {
                const rect = wrapRef.current.getBoundingClientRect();
                setTooltip({
                  x: e.clientX - rect.left,
                  y: e.clientY - rect.top,
                  label: meta.label,
                  rule: m.decision.triggeringRule,
                  ts: MockData.formatElapsed(m.atSec),
                  source: m.source,
                });
              }}
              onLeave={() => setTooltip(null)}
            />
          );
        })}

        {/* now-line */}
        <line
          x1={xFor(atSec)} y1={4}
          x2={xFor(atSec)} y2={markerY + 8}
          stroke="var(--text-primary)" strokeWidth="1" strokeDasharray="3,3" opacity="0.4"
        />
        {/* scrubber track */}
        <line x1={PAD_LEFT} y1={totalH - 8} x2={PAD_LEFT + innerW} y2={totalH - 8} stroke="var(--border-emphasis)" strokeWidth="1"/>
        <line x1={PAD_LEFT} y1={totalH - 8} x2={xFor(atSec)} y2={totalH - 8} stroke="var(--text-primary)" strokeWidth="1.5"/>
        <circle cx={xFor(atSec)} cy={totalH - 8} r="6" fill="var(--bg-primary)" stroke="var(--text-primary)" strokeWidth="1.5"/>
      </svg>
      {tooltip && (
        <div className="timeline-tooltip" style={{ left: tooltip.x + 10, top: tooltip.y - 40 }}>
          <div className="row gap-2">
            <strong>{tooltip.label}</strong>
            <span className="mono text-tertiary">{tooltip.ts}</span>
          </div>
          {tooltip.rule && <div className="mono text-11 text-tertiary">rule: {tooltip.rule}</div>}
          {tooltip.source !== 'auto' && <div className="text-11 text-tertiary">researcher · {tooltip.source.replace('researcher_', '')}</div>}
        </div>
      )}
    </div>
  );
}

function StateSnapshot({ atSec, sessionTickId }) {
  // build a deterministic-ish snapshot from base + scrub position
  const data = MockData.INITIAL_STATE;
  return (
    <div className="pane">
      <div className="split-pane-header row" style={{ justifyContent: 'space-between' }}>
        <h3>State at <span className="mono">{MockData.formatElapsed(atSec)}</span></h3>
        <span className="mono text-tertiary text-11">tick #{sessionTickId}</span>
      </div>
      <div className="split-pane-body scroll">
        <table className="state-table">
          <thead>
            <tr>
              <th>Participant</th>
              <th>Share 5m</th>
              <th>Turns</th>
              <th>Last spoke</th>
              <th>Flags</th>
            </tr>
          </thead>
          <tbody>
            {MockData.PARTICIPANTS.map((p) => {
              const s = data[p.id];
              const ramp = MockData.rampToVar(p.colorRamp);
              const over = s.actualShareLast5MinPct > s.fairSharePct + 10;
              const under = s.actualShareLast5MinPct < s.fairSharePct - 10;
              return (
                <tr key={p.id}>
                  <td>
                    <div className="row gap-2">
                      <ParticipantAvatar participant={p} size={20} />
                      <span style={{ fontWeight: 500 }}>{p.displayName.split(' ')[0]}</span>
                    </div>
                  </td>
                  <td className="num">
                    <span style={{ color: over ? 'var(--warning)' : under ? 'var(--info)' : undefined }}>
                      {s.actualShareLast5MinPct}%
                    </span>
                    <span className="text-tertiary"> / {s.fairSharePct}%</span>
                  </td>
                  <td className="num">{s.turnCount}</td>
                  <td className="num text-secondary">{s.lastSpokeAt ? s.lastSpokeAt.replace('now-', '') : '—'}</td>
                  <td>
                    {s.flags.length === 0 && <span className="text-tertiary">—</span>}
                    {s.flags.includes('dominating') && <span className="flag-pill dominating">dominating</span>}
                    {s.flags.includes('silent_too_long') && <span className="flag-pill silent">quiet</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div style={{ padding: '12px 16px' }}>
          <div className="text-12 text-tertiary">
            Snapshots are recorded every 500ms tick. Cite the tick ID in your notes for an exact match to engine state.
          </div>
        </div>
      </div>
    </div>
  );
}

function DecisionDetail({ decision }) {
  if (!decision) {
    return (
      <div className="pane">
        <div className="split-pane-header"><h3>Decision detail</h3></div>
        <div className="split-pane-body" style={{ padding: '24px 16px' }}>
          <p className="text-tertiary text-12">Click a marker on the timeline to inspect the decision.</p>
        </div>
      </div>
    );
  }
  const meta = MockData.actionMeta(decision.action);
  const target = decision.targetParticipantId ? MockData.PARTICIPANTS.find(p => p.id === decision.targetParticipantId) : null;
  const ruleEvals = MockData.buildRuleEvalsFor(decision);
  return (
    <div className="pane">
      <div className="split-pane-header row" style={{ justifyContent: 'space-between' }}>
        <h3>Decision at <span className="mono">{MockData.formatElapsed(decision.sessionElapsedSec || 0)}</span></h3>
        <Pill tone={meta.color === 'tertiary' ? 'default' : meta.color}>
          {meta.label}{target ? ` · ${target.displayName.split(' ')[0]}` : ''}
        </Pill>
      </div>
      <div className="split-pane-body scroll">
        <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {decision.llmOutput && (
            <div className={`spoken spoken-${meta.color}`} style={{ fontSize: 14, padding: '8px 12px' }}>
              "{decision.llmOutput}"
            </div>
          )}
          {!decision.llmOutput && decision.action === 'stay_silent' && (
            <div className="text-secondary" style={{ fontSize: 13.5 }}>
              {decision.reasonHuman}
            </div>
          )}
          <div className="col gap-1">
            <span className="field-label">Reason</span>
            <span className="text-secondary" style={{ fontSize: 13 }}>{decision.reasonHuman}</span>
          </div>
          <div className="col gap-1">
            <span className="field-label">Reason codes</span>
            <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
              {decision.reasonCodes.map((c) => (
                <span key={c} className="mono text-11" style={{ background: 'var(--bg-tertiary)', padding: '2px 6px', borderRadius: 4 }}>{c}</span>
              ))}
            </div>
          </div>
          {decision.researcherHint && (
            <div className="col gap-1">
              <span className="field-label">Researcher hint</span>
              <span className="text-secondary" style={{ fontSize: 13, fontStyle: 'italic' }}>"{decision.researcherHint}"</span>
            </div>
          )}
        </div>
        <div className="divider" />
        <div className="split-pane-header" style={{ borderTop: 'none' }}>
          <h3>Rules evaluated</h3>
          <span className="text-tertiary text-11 mono" style={{ marginLeft: 'auto' }}>{ruleEvals.length} rules · v1.0</span>
        </div>
        <div className="rule-eval">
          {ruleEvals.map((re) => (
            <div key={re.id} className="rule-eval-row" data-fired={re.fired ? 'true' : 'false'}>
              {re.fired
                ? <Icon name="check" size={13} className="icon-check" />
                : <Icon name="dash" size={13} className="icon-dash" />
              }
              <span className="name">{re.ruleName}</span>
              <span className="status">{re.statusText}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function FiltersPanel({ open, onClose, filters, setFilters }) {
  if (!open) return null;
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Filter timeline"
      width={460}
      footer={
        <>
          <button className="btn" onClick={() => setFilters({ actions: [], sourceFilter: 'all', flaggedOnly: false })}>Reset</button>
          <button className="btn btn-primary" onClick={onClose}>Apply</button>
        </>
      }
    >
      <div className="col gap-4">
        <div>
          <span className="field-label">Action type</span>
          <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
            {['prompt_participant', 'redirect_topic', 'suggest_turn_taking', 'summarize_thread', 'stay_silent'].map((a) => {
              const active = filters.actions.includes(a);
              const meta = MockData.actionMeta(a);
              return (
                <button
                  key={a}
                  className={`btn btn-sm ${active ? '' : 'btn-ghost'}`}
                  onClick={() => setFilters((f) => ({
                    ...f,
                    actions: active ? f.actions.filter(x => x !== a) : [...f.actions, a],
                  }))}
                  style={{ borderColor: active ? 'var(--border-emphasis)' : 'transparent' }}
                >
                  {active && <Icon name="check" size={12} />}
                  {meta.label}
                </button>
              );
            })}
          </div>
        </div>
        <div>
          <span className="field-label">Source</span>
          <div className="seg">
            {['all', 'auto', 'researcher'].map((s) => (
              <button
                key={s}
                aria-pressed={filters.sourceFilter === s}
                onClick={() => setFilters((f) => ({ ...f, sourceFilter: s }))}
                style={{ textTransform: 'capitalize' }}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
        <div className="row gap-2">
          <span
            className="toggle"
            data-on={filters.flaggedOnly ? 'true' : 'false'}
            onClick={() => setFilters((f) => ({ ...f, flaggedOnly: !f.flaggedOnly }))}
          />
          <label>Flagged moments only</label>
        </div>
      </div>
    </Modal>
  );
}

function ExportModal({ open, onClose }) {
  const [opts, setOpts] = useState({
    transcript: true, vtt: false, decisionLog: true, snapshots: false, audio: false, flaggedOnly: false,
  });
  const [stage, setStage] = useState('idle'); // idle | working | done
  const [progress, setProgress] = useState(0);

  function start() {
    setStage('working');
    setProgress(0);
    const id = setInterval(() => {
      setProgress((p) => {
        if (p >= 100) { clearInterval(id); setStage('done'); return 100; }
        return p + 7;
      });
    }, 120);
  }

  useEffect(() => { if (!open) { setStage('idle'); setProgress(0); } }, [open]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Export session"
      width={480}
      footer={
        stage === 'idle' ? (
          <>
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" onClick={start}>Generate export</button>
          </>
        ) : stage === 'done' ? (
          <>
            <button className="btn" onClick={onClose}>Close</button>
            <button className="btn btn-primary"><Icon name="download" size={14}/> Download .zip</button>
          </>
        ) : null
      }
    >
      {stage === 'idle' && (
        <div className="col gap-3">
          {[
            { k: 'transcript', label: 'Transcript', hint: '.txt' },
            { k: 'vtt', label: 'Transcript with timestamps', hint: '.vtt' },
            { k: 'decisionLog', label: 'Decision log', hint: '.csv' },
            { k: 'snapshots', label: 'State snapshots', hint: '.jsonl' },
            { k: 'audio', label: 'Audio recording', hint: '.mp3' },
          ].map((row) => (
            <label key={row.k} className="row gap-3" style={{ cursor: 'pointer' }}>
              <input
                type="checkbox" checked={opts[row.k]}
                onChange={() => setOpts((o) => ({ ...o, [row.k]: !o[row.k] }))}
              />
              <span style={{ flex: 1 }}>{row.label}</span>
              <span className="mono text-tertiary text-11">{row.hint}</span>
            </label>
          ))}
          <div className="divider"/>
          <label className="row gap-3" style={{ cursor: 'pointer' }}>
            <input type="checkbox" checked={opts.flaggedOnly} onChange={() => setOpts((o) => ({ ...o, flaggedOnly: !o.flaggedOnly }))}/>
            <span>Flagged moments only</span>
          </label>
        </div>
      )}
      {stage === 'working' && (
        <div className="col gap-3" style={{ padding: '12px 0' }}>
          <div className="text-secondary">Bundling export…</div>
          <div className="bar" style={{ height: 4, background: 'var(--bg-tertiary)', borderRadius: 9999, overflow: 'hidden' }}>
            <div style={{ width: `${progress}%`, height: '100%', background: 'var(--text-primary)', transition: 'width .12s linear' }} />
          </div>
          <div className="mono text-tertiary text-11">{progress}%</div>
        </div>
      )}
      {stage === 'done' && (
        <div className="col gap-2" style={{ padding: '12px 0' }}>
          <div className="row gap-2"><Icon name="check" size={16} className="icon-check"/> <span>Export ready</span></div>
          <span className="text-secondary text-12">verbio_session_4_2026-05-12.zip · 17.4 MB</span>
        </div>
      )}
    </Modal>
  );
}

function ReplayPage({ onNavigate, theme, onToggleTheme }) {
  const totalSec = 3481;
  const segments = useMemo(() => MockData.buildSpeechSegments(totalSec), [totalSec]);
  const markers = useMemo(() => MockData.buildReplayMarkers(), []);
  const scrub = useScrubber(totalSec);

  const [selectedMarker, setSelectedMarker] = useState(markers[2]?.id || null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [filters, setFilters] = useState({ actions: [], sourceFilter: 'all', flaggedOnly: false });

  const visibleMarkers = useMemo(() => {
    return markers.filter((m) => {
      if (filters.actions.length && !filters.actions.includes(m.action)) return false;
      if (filters.sourceFilter !== 'all') {
        if (filters.sourceFilter === 'auto' && m.source !== 'auto') return false;
        if (filters.sourceFilter === 'researcher' && m.source === 'auto') return false;
      }
      return true;
    });
  }, [markers, filters]);

  const selectedDecision = useMemo(() => {
    const m = markers.find((x) => x.id === selectedMarker);
    return m ? m.decision : null;
  }, [markers, selectedMarker]);

  // keyboard scrubbing
  useEffect(() => {
    function onKey(e) {
      if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT')) return;
      if (e.code === 'Space') { e.preventDefault(); scrub.setPlaying((p) => !p); }
      else if (e.code === 'ArrowLeft') { e.preventDefault(); scrub.setAtSec((s) => Math.max(0, s - (e.shiftKey ? 30 : 5))); }
      else if (e.code === 'ArrowRight') { e.preventDefault(); scrub.setAtSec((s) => Math.min(totalSec, s + (e.shiftKey ? 30 : 5))); }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [scrub.setPlaying, scrub.setAtSec, totalSec]); // eslint-disable-line

  return (
    <div className="replay-shell">
      <TopBar
        leadingPill={<span className="pill" style={{ background: 'var(--bg-tertiary)' }}><Icon name="archive" size={12} /> Replay</span>}
        crumbs={[
          { label: 'Studies', onClick: () => onNavigate('studies') },
          { label: 'AI assistants in knowledge work', onClick: () => onNavigate('studies') },
          { label: 'Session #3 · May 5' },
        ]}
      >
        <span className="mono text-tertiary text-12">
          {MockData.formatElapsed(scrub.atSec)} / {MockData.formatElapsed(totalSec)}
        </span>
        <button className="btn" onClick={() => setFiltersOpen(true)}>
          <Icon name="filter" size={13}/> Filter
          {(filters.actions.length || filters.sourceFilter !== 'all' || filters.flaggedOnly) && (
            <span className="pill info" style={{ padding: '0 5px', fontSize: 10 }}>
              {filters.actions.length + (filters.sourceFilter !== 'all' ? 1 : 0) + (filters.flaggedOnly ? 1 : 0)}
            </span>
          )}
        </button>
        <button className="btn" onClick={() => setExportOpen(true)}>
          <Icon name="download" size={13}/> Export
        </button>
        <button className="btn btn-ghost btn-icon" aria-label="Toggle theme" onClick={onToggleTheme}>
          <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={14} />
        </button>
      </TopBar>

      <div className="timeline-panel">
        <div className="timeline-toolbar">
          <button className="btn btn-icon" aria-label={scrub.playing ? 'Pause' : 'Play'} onClick={() => scrub.setPlaying((p) => !p)}>
            <Icon name={scrub.playing ? 'pause' : 'play'} size={14} />
          </button>
          <button className="btn btn-icon" aria-label="Rewind 30s" onClick={() => scrub.setAtSec((s) => Math.max(0, s - 30))}>
            <Icon name="rewind" size={14}/>
          </button>
          <span className="time mono">
            {MockData.formatElapsed(scrub.atSec)} <span className="text-tertiary"> / {MockData.formatElapsed(totalSec)}</span>
          </span>
          <span className="text-tertiary text-11">
            <span className="kbd">←</span><span className="kbd">→</span> seek · <span className="kbd">Shift</span>+arrow ±30s · <span className="kbd">Space</span> play/pause
          </span>
          <div className="speed" role="tablist">
            {[0.5, 1, 1.5, 2].map((v) => (
              <button key={v} aria-pressed={scrub.speed === v} onClick={() => scrub.setSpeed(v)}>{v}×</button>
            ))}
          </div>
        </div>
        <Timeline
          totalSec={totalSec}
          atSec={scrub.atSec}
          onSeek={scrub.setAtSec}
          markers={visibleMarkers}
          selectedMarker={selectedMarker}
          onSelectMarker={setSelectedMarker}
          segments={segments}
        />
        <div className="timeline-legend">
          <span className="item"><svg width="10" height="10"><circle cx="5" cy="5" r="4" fill="var(--info)"/></svg> prompt</span>
          <span className="item"><svg width="10" height="10"><rect x="1" y="1" width="8" height="8" fill="var(--accent-purple)" transform="rotate(45 5 5)"/></svg> redirect</span>
          <span className="item"><svg width="10" height="10"><polygon points="5,1 9,9 1,9" fill="var(--warning)"/></svg> turn-taking</span>
          <span className="item"><svg width="10" height="10"><polygon points="5,9 9,1 1,1" fill="var(--success)"/></svg> summary</span>
          <span className="item"><svg width="10" height="10"><circle cx="5" cy="5" r="4" fill="var(--text-primary)"/></svg> researcher</span>
          <span className="item text-tertiary">click any marker to inspect</span>
        </div>
      </div>

      <div className="replay-split">
        <StateSnapshot atSec={scrub.atSec} sessionTickId={Math.floor(scrub.atSec * 2) + 1240} />
        <DecisionDetail decision={selectedDecision} />
      </div>

      <FiltersPanel
        open={filtersOpen}
        onClose={() => setFiltersOpen(false)}
        filters={filters}
        setFilters={setFilters}
      />
      <ExportModal open={exportOpen} onClose={() => setExportOpen(false)} />
    </div>
  );
}

window.ReplayPage = ReplayPage;
