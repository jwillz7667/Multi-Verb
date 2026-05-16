import { describe, expect, it } from 'vitest';

import { GET, type HealthResponse } from './route.js';

describe('GET /api/health', () => {
  it('returns 200 with the expected envelope', async () => {
    const response = GET();

    expect(response.status).toBe(200);
    const body = (await response.json()) as HealthResponse;
    expect(body.status).toBe('ok');
    expect(body.service).toBe('verbio-web');
    expect(body.version).toMatch(/^\d+\.\d+\.\d+/);
    expect(['development', 'production', 'test']).toContain(body.environment);
  });

  it('includes an ISO-8601 timestamp', async () => {
    const before = Date.now();
    const response = GET();
    const after = Date.now();

    const body = (await response.json()) as HealthResponse;
    const ts = Date.parse(body.timestamp);
    expect(Number.isNaN(ts)).toBe(false);
    expect(ts).toBeGreaterThanOrEqual(before);
    expect(ts).toBeLessThanOrEqual(after);
  });
});
