import Link from 'next/link';

export default function HomePage(): React.ReactElement {
  return (
    <main className="min-h-screen bg-surface-secondary text-text-primary">
      <div className="mx-auto flex max-w-3xl flex-col gap-6 px-6 py-24">
        <div className="flex items-center gap-3">
          <svg
            width="28"
            height="28"
            viewBox="0 0 22 22"
            aria-hidden="true"
            className="text-text-primary"
          >
            <circle cx="11" cy="11" r="9" fill="none" stroke="currentColor" strokeWidth="1.2" />
            <path
              d="M6 9 c2 0 2 2 5 2 s3-2 5-2"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.2"
              strokeLinecap="round"
            />
            <circle cx="11" cy="11" r="1.4" fill="currentColor" />
          </svg>
          <span className="text-2xl font-medium tracking-tight">verbio</span>
        </div>

        <h1 className="text-3xl font-medium leading-tight">Phase 0 scaffold ready.</h1>

        <p className="text-text-secondary leading-relaxed">
          The dashboard, studies workspace, live moderator control, and replay surfaces will land in
          subsequent phases. This page exists so the deployment pipeline (Vercel + Railway +
          Postgres + Redis + R2) has something to render while infrastructure is being wired up.
        </p>

        <div className="border-border-default bg-surface-primary mt-4 grid grid-cols-2 gap-3 rounded-lg border p-4 text-sm">
          <ServiceStatus
            label="verbio-web"
            href="/api/health"
            description="Next.js liveness — this app, right now."
          />
          <ServiceStatus
            label="verbio-engine"
            href="http://localhost:8000/health"
            description="FastAPI liveness — run via `uv run verbio-engine` in services/engine."
            external
          />
        </div>

        <p className="text-text-tertiary mt-4 text-xs">
          Phase 0 of the engineering brief. The visual system here mirrors the handoff design tokens
          (IBM Plex Sans, light + dark themes via <code className="font-mono">data-theme</code>).
        </p>
      </div>
    </main>
  );
}

interface ServiceStatusProps {
  label: string;
  href: string;
  description: string;
  external?: boolean;
}

function ServiceStatus({
  label,
  href,
  description,
  external = false,
}: ServiceStatusProps): React.ReactElement {
  const content = (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-text-primary">{label}</span>
      <span className="text-text-tertiary text-xs">{description}</span>
    </div>
  );

  if (external) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="hover:bg-surface-tertiary rounded-md p-2 transition-colors"
      >
        {content}
      </a>
    );
  }

  return (
    <Link href={href} className="hover:bg-surface-tertiary rounded-md p-2 transition-colors">
      {content}
    </Link>
  );
}
