// ─────────────────────────────────────────────────────────────
// Shared UI primitives: icons, sidebar, topbar, simple controls.
// ─────────────────────────────────────────────────────────────

const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect } = React;

// ───────── lucide-style inline icons (only what we need) ─────────
function Icon({ name, size = 16, strokeWidth = 1.5, className }) {
  const s = size;
  const common = {
    width: s, height: s, viewBox: '0 0 24 24',
    fill: 'none', stroke: 'currentColor',
    strokeWidth, strokeLinecap: 'round', strokeLinejoin: 'round',
    className,
    'aria-hidden': true,
  };
  switch (name) {
    case 'studies':
      return <svg {...common}><path d="M4 4h8v8H4z"/><path d="M14 4h6v4h-6z"/><path d="M14 10h6v10h-6z"/><path d="M4 14h8v6H4z"/></svg>;
    case 'sessions':
      return <svg {...common}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>;
    case 'insights':
      return <svg {...common}><path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M22 20v-4"/></svg>;
    case 'settings':
      return <svg {...common}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 0 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 0 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 0 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 0 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>;
    case 'plus': return <svg {...common}><path d="M12 5v14"/><path d="M5 12h14"/></svg>;
    case 'search': return <svg {...common}><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>;
    case 'chevron-down': return <svg {...common}><path d="m6 9 6 6 6-6"/></svg>;
    case 'chevron-right': return <svg {...common}><path d="m9 6 6 6-6 6"/></svg>;
    case 'chevron-left': return <svg {...common}><path d="m15 6-6 6 6 6"/></svg>;
    case 'more': return <svg {...common}><circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/></svg>;
    case 'flag': return <svg {...common}><path d="M4 22V4"/><path d="M4 4h13l-2 4 2 4H4"/></svg>;
    case 'mic': return <svg {...common}><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/></svg>;
    case 'mic-off': return <svg {...common}><path d="M9 9v2a3 3 0 0 0 5 2.3"/><path d="M15 11V6a3 3 0 0 0-5.7-1.3"/><path d="M5 11a7 7 0 0 0 9.6 6.5"/><path d="M19 11a7 7 0 0 1-1.4 4.2"/><path d="M12 18v3"/><path d="M3 3l18 18"/></svg>;
    case 'pause': return <svg {...common}><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg>;
    case 'play': return <svg {...common}><polygon points="6,4 20,12 6,20"/></svg>;
    case 'archive': return <svg {...common}><path d="M3 7h18v4H3z"/><path d="M5 11v9h14v-9"/><path d="M10 14h4"/></svg>;
    case 'download': return <svg {...common}><path d="M12 4v12"/><path d="m7 11 5 5 5-5"/><path d="M5 20h14"/></svg>;
    case 'filter': return <svg {...common}><path d="M4 5h16l-6 8v5l-4 2v-7z"/></svg>;
    case 'sun': return <svg {...common}><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m4.93 19.07 1.41-1.41"/><path d="m17.66 6.34 1.41-1.41"/></svg>;
    case 'moon': return <svg {...common}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>;
    case 'x': return <svg {...common}><path d="m6 6 12 12"/><path d="m18 6-12 12"/></svg>;
    case 'check': return <svg {...common}><path d="m5 12 5 5 9-11"/></svg>;
    case 'dash': return <svg {...common}><path d="M6 12h12"/></svg>;
    case 'sliders': return <svg {...common}><path d="M4 6h8"/><path d="M16 6h4"/><circle cx="14" cy="6" r="2"/><path d="M4 12h2"/><path d="M10 12h10"/><circle cx="8" cy="12" r="2"/><path d="M4 18h12"/><path d="M20 18h0"/><circle cx="18" cy="18" r="2"/></svg>;
    case 'arrow-down': return <svg {...common}><path d="M12 5v14"/><path d="m6 13 6 6 6-6"/></svg>;
    case 'send': return <svg {...common}><path d="M22 2L11 13"/><path d="m22 2-7 20-4-9-9-4 20-7z"/></svg>;
    case 'speaker': return <svg {...common}><path d="M11 5L6 9H2v6h4l5 4z"/><path d="M15 9a4 4 0 0 1 0 6"/><path d="M19 6a8 8 0 0 1 0 12"/></svg>;
    case 'whisper': return <svg {...common}><path d="M3 12c1-3 4-5 9-5s8 2 9 5"/><path d="M3 12c1 3 4 5 9 5s8-2 9-5"/><circle cx="12" cy="12" r="2"/></svg>;
    case 'redirect': return <svg {...common}><path d="M3 12h13"/><path d="m12 7 5 5-5 5"/><path d="M19 4v16"/></svg>;
    case 'prompt': return <svg {...common}><path d="M4 5h16v11H7l-3 3z"/></svg>;
    case 'rewind': return <svg {...common}><path d="m11 19-9-7 9-7v14z"/><path d="m22 19-9-7 9-7v14z"/></svg>;
    default: return null;
  }
}

// ───────── small primitives ─────────
function Pill({ children, tone = 'default', dot = false, className = '' }) {
  return (
    <span className={`pill ${tone}${className ? ' ' + className : ''}`}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}

function ParticipantAvatar({ participant, size = 24 }) {
  const ramp = MockData.rampToVar(participant.colorRamp);
  return (
    <span
      className="tile-avatar"
      style={{
        width: size, height: size,
        background: ramp.bg,
        color: ramp.color,
        fontSize: Math.max(9, Math.floor(size * 0.42)),
      }}
      aria-hidden="true"
    >
      {participant.initials}
    </span>
  );
}

function LivePill() {
  return (
    <span className="live-pill" aria-live="off">
      <span className="dot" />
      Live
    </span>
  );
}

// ───────── sidebar ─────────
function Sidebar({ current, onNavigate, theme, onToggleTheme }) {
  const items = [
    { key: 'studies',  label: 'Studies',  icon: 'studies' },
    { key: 'sessions', label: 'Sessions', icon: 'sessions' },
    { key: 'insights', label: 'Insights', icon: 'insights', disabled: true },
    { key: 'settings', label: 'Settings', icon: 'settings' },
  ];
  return (
    <aside className="sidebar" aria-label="Primary navigation">
      <div className="wordmark">
        {/* custom verbio mark */}
        <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
          <circle cx="11" cy="11" r="9" fill="none" stroke="currentColor" strokeWidth="1.2"/>
          <path d="M6 9 c2 0 2 2 5 2 s3-2 5-2" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
          <circle cx="11" cy="11" r="1.4" fill="currentColor"/>
        </svg>
        <span className="wordmark-text">verbio</span>
      </div>

      <nav className="nav" aria-label="Main">
        {items.map((item) => (
          <button
            key={item.key}
            className={`nav-item${item.disabled ? ' disabled' : ''}`}
            aria-current={current === item.key ? 'page' : undefined}
            onClick={() => !item.disabled && onNavigate(item.key)}
            disabled={item.disabled}
          >
            <Icon name={item.icon} size={15} />
            <span>{item.label}</span>
            {item.disabled && <span className="pill-coming">soon</span>}
          </button>
        ))}
      </nav>

      <div className="sidebar-section-label">Recent</div>
      <nav className="nav" aria-label="Recent">
        <button className="nav-item" onClick={() => onNavigate('live')}>
          <span className="live-pill" style={{ padding: '1px 6px', fontSize: 9 }}>
            <span className="dot" /> Live
          </span>
          <span style={{ marginLeft: 4 }}>AI assistants — #4</span>
        </button>
        <button className="nav-item" onClick={() => onNavigate('replay')}>
          <Icon name="archive" size={15} />
          <span>AI assistants — #3</span>
        </button>
      </nav>

      <div className="footer">
        <button className="org" aria-label="Organization">
          <span className="org-mark">NL</span>
          <span className="col" style={{ alignItems: 'flex-start', lineHeight: 1.2 }}>
            <span style={{ fontWeight: 500 }}>Northlake Research</span>
            <span className="text-tertiary" style={{ fontSize: 11 }}>3 workspaces</span>
          </span>
        </button>
        <button className="user" aria-label="Account">
          <span className="avatar">LR</span>
          <span className="col" style={{ alignItems: 'flex-start', lineHeight: 1.2, flex: 1 }}>
            <span>Lex Romero</span>
            <span className="text-tertiary" style={{ fontSize: 11 }}>lex@northlake.ai</span>
          </span>
          <button
            className="btn btn-ghost btn-icon"
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            onClick={(e) => { e.stopPropagation(); onToggleTheme(); }}
            title="Toggle theme"
          >
            <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={15} />
          </button>
        </button>
      </div>
    </aside>
  );
}

// ───────── topbar ─────────
function TopBar({ crumbs, children, leadingPill }) {
  return (
    <header className="topbar">
      {leadingPill}
      <div className="crumbs">
        {crumbs.map((c, i) => (
          <React.Fragment key={i}>
            {i > 0 && <span className="sep">/</span>}
            {c.onClick ? (
              <button
                className={`crumb-back${i === crumbs.length - 1 ? ' current' : ''}`}
                onClick={c.onClick}
                style={{ padding: '2px 6px' }}
              >
                {c.label}
              </button>
            ) : (
              <span className={i === crumbs.length - 1 ? 'current' : ''}>{c.label}</span>
            )}
          </React.Fragment>
        ))}
      </div>
      <div className="actions">{children}</div>
    </header>
  );
}

// ───────── modal ─────────
function Modal({ open, onClose, title, children, footer, width }) {
  useEffect(() => {
    if (!open) return;
    function esc(e) { if (e.key === 'Escape') onClose(); }
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="modal-scrim" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal" style={width ? { maxWidth: width } : undefined} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header row" style={{ justifyContent: 'space-between' }}>
          <h2>{title}</h2>
          <button className="btn btn-ghost btn-icon" aria-label="Close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

// ───────── tweaks panel (custom for this build) ─────────
function TweaksPanel({ open, onClose, tweaks, setTweak }) {
  if (!open) return null;
  return (
    <div className="tweaks" role="dialog" aria-label="Tweaks">
      <div className="tweaks-header">
        <h4>Tweaks</h4>
        <button className="close" aria-label="Close tweaks" onClick={onClose}>
          <Icon name="x" size={14} />
        </button>
      </div>
      <div className="tweaks-body">
        <div className="tweak-row">
          <label htmlFor="tw-theme">Theme</label>
          <div className="seg" id="tw-theme">
            <button aria-pressed={tweaks.theme === 'light'} onClick={() => setTweak('theme', 'light')}>Light</button>
            <button aria-pressed={tweaks.theme === 'dark'} onClick={() => setTweak('theme', 'dark')}>Dark</button>
          </div>
        </div>
        <div className="tweak-row">
          <label htmlFor="tw-sim">Sim speed</label>
          <div className="seg" id="tw-sim">
            <button aria-pressed={tweaks.simSpeed === 0.5} onClick={() => setTweak('simSpeed', 0.5)}>0.5×</button>
            <button aria-pressed={tweaks.simSpeed === 1} onClick={() => setTweak('simSpeed', 1)}>1×</button>
            <button aria-pressed={tweaks.simSpeed === 2} onClick={() => setTweak('simSpeed', 2)}>2×</button>
          </div>
        </div>
        <div className="tweak-row">
          <label htmlFor="tw-density">Density</label>
          <div className="seg" id="tw-density">
            <button aria-pressed={tweaks.density === 'compact'} onClick={() => setTweak('density', 'compact')}>Compact</button>
            <button aria-pressed={tweaks.density === 'comfortable'} onClick={() => setTweak('density', 'comfortable')}>Comfortable</button>
          </div>
        </div>
        <div className="tweak-row">
          <label htmlFor="tw-quietness">Quietness budget</label>
          <span className="mono text-12 text-secondary">{tweaks.quietness}</span>
        </div>
        <input
          id="tw-quietness"
          type="range" min={1} max={10} step={1}
          className="slider"
          value={tweaks.quietness}
          onChange={(e) => setTweak('quietness', Number(e.target.value))}
        />
        <div className="tweak-row" style={{ marginTop: 4 }}>
          <span className="text-tertiary text-11">P prompt · R redirect · W whisper · F flag · M mute · Space pause</span>
        </div>
      </div>
    </div>
  );
}

// expose
Object.assign(window, {
  Icon, Pill, ParticipantAvatar, LivePill, Sidebar, TopBar, Modal, TweaksPanel,
});
