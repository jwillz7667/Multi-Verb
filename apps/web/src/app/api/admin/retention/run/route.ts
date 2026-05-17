/**
 * POST /api/admin/retention/run — the daily retention sweep.
 *
 * Invoked by Vercel Cron (see `vercel.json`) once per day at 04:00
 * UTC. The platform sends `Authorization: Bearer ${CRON_SECRET}`;
 * any caller missing that header is 401'd.
 *
 * The actual sweep logic lives in `features/retention/runner.ts` —
 * this handler's job is auth + transport. We POST rather than GET
 * because (a) cron jobs traditionally side-effect, (b) it discourages
 * the curl-from-browser footgun, and (c) it keeps cache layers from
 * deciding to serve a stale 200.
 *
 * Status codes:
 *   - 200 — sweep completed (possibly with per-session errors —
 *           check the `errors` array in the body),
 *   - 401 — missing or wrong bearer token,
 *   - 503 — `CRON_SECRET` not configured in env (deployment misstep;
 *           we 503 rather than 401 so the cron retries),
 *   - 500 — unrecoverable failure before the sweep could start
 *           (Prisma / R2 init failures bubble here).
 */

import { NextResponse } from 'next/server';

import {
  createPrismaRetentionRepo,
  defaultR2KeyDeleter,
  runRetention,
  type RetentionLogger,
} from '@/features/retention';
import { serverEnv } from '@/lib/env';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
// Retention can touch many sessions; raise the function timeout above
// the default 10s (Vercel Hobby) / 60s (Pro). 300s = 5 min matches
// Vercel Pro's maxDuration cap; on Hobby this is silently clamped.
export const maxDuration = 300;

export async function POST(request: Request): Promise<NextResponse> {
  const secret = serverEnv?.CRON_SECRET;
  if (secret === undefined || secret.length === 0) {
    return NextResponse.json(
      { error: 'cron_secret_not_configured' },
      // 503 (not 401) so Vercel Cron retries the next day rather than
      // permanently shelving this as "client error".
      { status: 503 },
    );
  }

  const header = request.headers.get('authorization');
  if (header !== `Bearer ${secret}`) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  // `console`-backed logger keeps structured events flowing into
  // Vercel's log drain; tests inject a vi.fn via the runner directly,
  // so the production route doesn't need DI here. The project's
  // `no-console` rule allows only `warn`/`error`, so info events route
  // through `console.warn` — they're operationally interesting (daily
  // sweep results) so the level is defensible either way.
  const logger: RetentionLogger = {
    info: (message, fields) => {
      console.warn(`[retention] ${message}`, fields ?? {});
    },
    warn: (message, fields) => {
      console.warn(`[retention] ${message}`, fields ?? {});
    },
    error: (message, fields) => {
      console.error(`[retention] ${message}`, fields ?? {});
    },
  };

  const repo = createPrismaRetentionRepo();
  const result = await runRetention(repo, defaultR2KeyDeleter, { logger });

  return NextResponse.json(
    {
      ok: true,
      summary: {
        studiesProcessed: result.studiesProcessed,
        sessionsProcessed: result.sessionsProcessed,
        sessionsSkipped: result.sessionsSkipped,
        snapshotsDeleted: result.snapshotsDeleted,
        recordingsDeleted: result.recordingsDeleted,
        transcriptsDeleted: result.transcriptsDeleted,
        errorCount: result.errors.length,
      },
      // Echo errors verbatim so the daily Vercel log row is
      // grep-friendly when something needs ops attention.
      errors: result.errors,
    },
    { status: 200 },
  );
}
