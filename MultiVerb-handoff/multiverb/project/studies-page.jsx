// ─────────────────────────────────────────────────────────────
// Studies list page
// ─────────────────────────────────────────────────────────────

function StudiesPage({ onNavigate, theme, onToggleTheme }) {
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [sort, setSort] = useState('updated');

  const studies = useMemo(() => {
    let s = MockData.STUDIES.slice();
    if (statusFilter !== 'all') s = s.filter((x) => x.status === statusFilter);
    if (query.trim()) {
      const q = query.toLowerCase();
      s = s.filter((x) => x.name.toLowerCase().includes(q) || x.prompt.toLowerCase().includes(q) || x.tags.some((t) => t.includes(q)));
    }
    s.sort((a, b) => (b.lastSessionDate || '').localeCompare(a.lastSessionDate || ''));
    return s;
  }, [query, statusFilter, sort]);

  return (
    <div className="page">
      <TopBar crumbs={[{ label: 'Studies' }]}>
        <button className="btn btn-ghost btn-icon" aria-label="Toggle theme" onClick={onToggleTheme}>
          <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={15} />
        </button>
        <button className="btn btn-primary"><Icon name="plus" size={14} /> New study</button>
      </TopBar>
      <div className="page-body scroll">
        <div className="container">
          <div className="studies-header">
            <div>
              <h1>Studies</h1>
              <p className="sub">Configure and run moderated research sessions.</p>
            </div>
          </div>

          <div className="filter-bar">
            <div style={{ position: 'relative', flex: '0 0 260px' }}>
              <Icon name="search" size={14} className="absolute" />
              <input
                className="input"
                placeholder="Search studies, prompts, tags"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                style={{ paddingLeft: 30 }}
              />
              <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)', pointerEvents: 'none' }}>
                <Icon name="search" size={14} />
              </span>
            </div>
            <div className="seg" role="tablist">
              {['all', 'active', 'draft', 'archived'].map((s) => (
                <button
                  key={s}
                  aria-pressed={statusFilter === s}
                  onClick={() => setStatusFilter(s)}
                  style={{ textTransform: 'capitalize' }}
                >
                  {s}
                </button>
              ))}
            </div>
            <div style={{ marginLeft: 'auto' }} className="row gap-2">
              <span className="text-tertiary text-12">{studies.length} studies</span>
            </div>
          </div>

          <div className="card" style={{ overflow: 'hidden' }}>
            {studies.length === 0 ? (
              <div className="col" style={{ alignItems: 'center', padding: '64px 16px', gap: 12 }}>
                <svg width="76" height="60" viewBox="0 0 76 60" fill="none" stroke="currentColor" strokeWidth="1" style={{ color: 'var(--text-tertiary)' }}>
                  <rect x="6" y="10" width="48" height="44" rx="4"/>
                  <rect x="22" y="6" width="48" height="44" rx="4" fill="var(--bg-secondary)"/>
                  <path d="M30 24h30"/><path d="M30 32h22"/><path d="M30 40h26"/>
                </svg>
                <h3>No studies match your filters</h3>
                <p className="text-secondary">Try a different status, or start something new.</p>
                <button className="btn btn-primary"><Icon name="plus" size={14}/> New study</button>
              </div>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th style={{ width: '28%' }}>Name</th>
                    <th>Prompt</th>
                    <th style={{ width: 96 }}>Sessions</th>
                    <th style={{ width: 132 }}>Last session</th>
                    <th style={{ width: 96 }}>Status</th>
                    <th style={{ width: 60 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {studies.map((s) => {
                    const tone = s.status === 'active' ? 'success' : s.status === 'draft' ? 'warning' : 'default';
                    return (
                      <tr key={s.id} className="clickable" onClick={() => onNavigate(s.id === 'st_001' ? 'live' : 'studies')}>
                        <td>
                          <div className="col gap-1">
                            <span className="fw-500">{s.name}</span>
                            <div className="row gap-2">
                              {s.tags.map((t) => <Pill key={t} tone="default">{t}</Pill>)}
                            </div>
                          </div>
                        </td>
                        <td>
                          <p className="text-secondary" style={{
                            display: '-webkit-box',
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: 'vertical',
                            overflow: 'hidden',
                            maxWidth: 460,
                            fontSize: 13,
                          }}>{s.prompt}</p>
                        </td>
                        <td className="mono">{s.sessionCount}</td>
                        <td className="text-secondary text-12">{s.lastSessionDate || '—'}</td>
                        <td><Pill tone={tone} dot>{s.status}</Pill></td>
                        <td>
                          <button className="btn btn-ghost btn-icon" aria-label="Actions" onClick={(e) => e.stopPropagation()}>
                            <Icon name="more" size={16} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>

          <div className="row gap-2" style={{ marginTop: 20, color: 'var(--text-tertiary)' }}>
            <span className="text-12">Tip — open the study to start a new session or review past recordings.</span>
          </div>
        </div>
      </div>
    </div>
  );
}

window.StudiesPage = StudiesPage;
