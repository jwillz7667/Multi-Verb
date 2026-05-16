/**
 * Readiness probe. Checks the dependencies the web service needs to
 * actually serve a useful request (Postgres, Redis). Returns 200 with
 * per-check status when every dependency is reachable; 503 if any
 * required dependency is down.
 *
 * Phase 0: dependency clients are not yet wired (DB + Redis lands in
 * the Auth.js / Prisma commit). The endpoint reports each check as
 * `"skip"` with `status: "ready"` so deploy gating passes; later
 * phases will swap each `runCheck` body for a real ping.
 */

import { NextResponse } from 'next/server';

import { SERVICE_NAME, SERVICE_VERSION } from '@/lib/service-info';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

export type CheckStatus = 'ok' | 'skip' | 'fail';

export interface ReadinessCheck {
  status: CheckStatus;
  message?: string;
}

export interface ReadinessResponse {
  status: 'ready' | 'not_ready';
  service: string;
  version: string;
  environment: string;
  timestamp: string;
  checks: {
    postgres: ReadinessCheck;
    redis: ReadinessCheck;
    engine: ReadinessCheck;
  };
}

async function runCheck(label: string): Promise<ReadinessCheck> {
  await Promise.resolve();
  return {
    status: 'skip',
    message: `${label} probe not yet wired (Phase 0 scaffold)`,
  };
}

export async function GET(): Promise<NextResponse<ReadinessResponse>> {
  const [postgres, redis, engine] = await Promise.all([
    runCheck('postgres'),
    runCheck('redis'),
    runCheck('engine'),
  ]);

  const overall: ReadinessResponse['status'] = [postgres, redis, engine].some(
    (c) => c.status === 'fail',
  )
    ? 'not_ready'
    : 'ready';

  const body: ReadinessResponse = {
    status: overall,
    service: SERVICE_NAME,
    version: SERVICE_VERSION,
    environment: process.env.NODE_ENV,
    timestamp: new Date().toISOString(),
    checks: { postgres, redis, engine },
  };

  const httpStatus = overall === 'ready' ? 200 : 503;
  return NextResponse.json(body, { status: httpStatus });
}
