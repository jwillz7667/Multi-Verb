// ─────────────────────────────────────────────────────────────
// App shell — sidebar routing, theme, tweaks panel.
// ─────────────────────────────────────────────────────────────

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "simSpeed": 1,
  "density": "comfortable",
  "quietness": 5
}/*EDITMODE-END*/;

function App() {
  const [route, setRoute] = useState(() => {
    const h = window.location.hash.replace('#', '');
    return ['studies','live','replay','sessions','settings'].includes(h) ? h : 'studies';
  });
  const [tweaks, setTweaks] = useState(TWEAK_DEFAULTS);
  const [tweaksOpen, setTweaksOpen] = useState(false);

  // theme on root
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', tweaks.theme);
  }, [tweaks.theme]);

  // route -> hash
  useEffect(() => {
    window.location.hash = route;
  }, [route]);

  // hash change -> route
  useEffect(() => {
    function onHash() {
      const h = window.location.hash.replace('#', '');
      if (['studies','live','replay','sessions','settings'].includes(h)) setRoute(h);
    }
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  // edit-mode protocol
  useEffect(() => {
    function onMessage(e) {
      if (!e.data) return;
      if (e.data.type === '__activate_edit_mode') setTweaksOpen(true);
      else if (e.data.type === '__deactivate_edit_mode') setTweaksOpen(false);
    }
    window.addEventListener('message', onMessage);
    try { window.parent.postMessage({ type: '__edit_mode_available' }, '*'); } catch {}
    return () => window.removeEventListener('message', onMessage);
  }, []);

  const setTweak = useCallback((key, val) => {
    setTweaks((t) => {
      const next = typeof key === 'object' ? { ...t, ...key } : { ...t, [key]: val };
      try {
        window.parent.postMessage({ type: '__edit_mode_set_keys', edits: typeof key === 'object' ? key : { [key]: val } }, '*');
      } catch {}
      return next;
    });
  }, []);

  function toggleTheme() {
    setTweak('theme', tweaks.theme === 'dark' ? 'light' : 'dark');
  }

  const immersive = route === 'live' || route === 'replay';

  let body;
  switch (route) {
    case 'live':
      body = <LiveControlPage onNavigate={setRoute} theme={tweaks.theme} onToggleTheme={toggleTheme} tweaks={tweaks} />;
      break;
    case 'replay':
      body = <ReplayPage onNavigate={setRoute} theme={tweaks.theme} onToggleTheme={toggleTheme} />;
      break;
    case 'sessions':
    case 'settings':
      body = (
        <div className="page">
          <TopBar crumbs={[{ label: route.charAt(0).toUpperCase() + route.slice(1) }]}>
            <button className="btn btn-ghost btn-icon" aria-label="Toggle theme" onClick={toggleTheme}>
              <Icon name={tweaks.theme === 'dark' ? 'sun' : 'moon'} size={15} />
            </button>
          </TopBar>
          <div className="page-body scroll">
            <div className="container">
              <h1 style={{ marginBottom: 8 }}>{route.charAt(0).toUpperCase() + route.slice(1)}</h1>
              <p className="text-secondary" style={{ marginBottom: 24 }}>This surface isn't part of the prototype focus. Try Studies or jump straight into the Live session below.</p>
              <div className="row gap-2">
                <button className="btn btn-primary" onClick={() => setRoute('studies')}>Go to studies</button>
                <button className="btn" onClick={() => setRoute('live')}>Open live session</button>
                <button className="btn" onClick={() => setRoute('replay')}>Open replay</button>
              </div>
            </div>
          </div>
        </div>
      );
      break;
    default:
      body = <StudiesPage onNavigate={setRoute} theme={tweaks.theme} onToggleTheme={toggleTheme} />;
  }

  return (
    <div className="app" data-immersive={immersive} data-density={tweaks.density}>
      {!immersive && (
        <Sidebar
          current={route}
          onNavigate={setRoute}
          theme={tweaks.theme}
          onToggleTheme={toggleTheme}
        />
      )}
      {body}
      <TweaksPanel
        open={tweaksOpen}
        onClose={() => {
          setTweaksOpen(false);
          try { window.parent.postMessage({ type: '__edit_mode_dismissed' }, '*'); } catch {}
        }}
        tweaks={tweaks}
        setTweak={setTweak}
      />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
