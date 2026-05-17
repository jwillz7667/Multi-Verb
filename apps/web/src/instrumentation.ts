/**
 * Next.js instrumentation hook (P7 L4).
 *
 * Next.js calls `register()` once per worker before any request is
 * served — the canonical place to wire process-wide observability
 * (Sentry, OpenTelemetry, etc.) without polluting per-route handlers.
 *
 * Sentry is initialised here lazily and only when `SENTRY_DSN` is set,
 * mirroring the engine side. With no DSN this hook is a no-op so local
 * dev + ephemeral preview deploys don't need real ingest credentials.
 *
 * We dynamic-import per runtime — `@sentry/nextjs` ships separate
 * entrypoints for the Node + edge runtimes and importing the wrong one
 * crashes the matching worker on boot. The runtime check is the
 * official pattern from Sentry's Next.js 15 docs.
 *
 * Path: `src/instrumentation.ts` is picked up automatically because
 * `next.config.ts` defaults `instrumentationHook` on for App Router
 * projects on Next.js 15+. No additional config knob needed.
 */

export async function register(): Promise<void> {
  if (!process.env['SENTRY_DSN']) return;

  const environment = process.env['SENTRY_ENVIRONMENT'] ?? process.env.NODE_ENV;
  const sampleRaw = process.env['SENTRY_TRACES_SAMPLE_RATE'];
  const tracesSampleRate = sampleRaw === undefined ? 0 : Number(sampleRaw);

  if (process.env['NEXT_RUNTIME'] === 'nodejs') {
    const Sentry = await import('@sentry/nextjs');
    Sentry.init({
      dsn: process.env['SENTRY_DSN'],
      environment,
      tracesSampleRate,
      // The Verbio web service is a thin orchestrator; most actual work
      // happens in the engine. Defaulting `debug` off keeps stdout
      // clean — flip via env if a deploy goes sideways.
      debug: false,
    });
  }

  if (process.env['NEXT_RUNTIME'] === 'edge') {
    const Sentry = await import('@sentry/nextjs');
    Sentry.init({
      dsn: process.env['SENTRY_DSN'],
      environment,
      tracesSampleRate,
      debug: false,
    });
  }
}

/**
 * Forwards unhandled request errors to Sentry. Next.js 15 calls this
 * for errors thrown in server components, route handlers, and server
 * actions that escape the normal try/catch path. Without this hook
 * those errors would only be visible in deployment logs.
 */
export async function onRequestError(
  err: unknown,
  request: {
    path: string;
    method: string;
    headers: Record<string, string | string[] | undefined>;
  },
  context: {
    routerKind: 'Pages Router' | 'App Router';
    routePath: string;
    routeType: 'render' | 'route' | 'action' | 'middleware';
  },
): Promise<void> {
  if (!process.env['SENTRY_DSN']) return;
  const Sentry = await import('@sentry/nextjs');
  Sentry.captureRequestError(err, request, context);
}
