import { describe, expect, it } from 'vitest';

import { GET, type ReadinessResponse } from './route.js';

describe('GET /api/ready', () => {
  it('returns 200 with status=ready when no checks fail', async () => {
    const response = await GET();

    expect(response.status).toBe(200);
    const body = (await response.json()) as ReadinessResponse;
    expect(body.status).toBe('ready');
    expect(body.service).toBe('verbio-web');
  });

  it('reports all three dependency checks', async () => {
    const response = await GET();

    const body = (await response.json()) as ReadinessResponse;
    expect(body.checks.postgres.status).toBe('skip');
    expect(body.checks.redis.status).toBe('skip');
    expect(body.checks.engine.status).toBe('skip');
  });

  it('marks Phase 0 checks as skipped with a descriptive message', async () => {
    const response = await GET();

    const body = (await response.json()) as ReadinessResponse;
    expect(body.checks.postgres.message).toMatch(/Phase 0/);
  });
});
