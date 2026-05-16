/**
 * Liveness probe. Returns 200 with build info as long as the Next.js
 * runtime is up. Does not touch the database, Redis, or any other
 * dependency — readiness lives at /api/ready.
 *
 * Vercel pings this for health gating; Railway-side engine has the
 * same surface so dashboards can correlate.
 */

import { NextResponse } from 'next/server';

import { SERVICE_NAME, SERVICE_VERSION } from '@/lib/service-info.js';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

export interface HealthResponse {
  status: 'ok';
  service: string;
  version: string;
  environment: string;
  timestamp: string;
}

export function GET(): NextResponse<HealthResponse> {
  const body: HealthResponse = {
    status: 'ok',
    service: SERVICE_NAME,
    version: SERVICE_VERSION,
    environment: process.env.NODE_ENV ?? 'development',
    timestamp: new Date().toISOString(),
  };
  return NextResponse.json(body);
}
