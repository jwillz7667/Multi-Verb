/**
 * Boundary tests for POST /api/livekit/webhook.
 *
 * Pins:
 *   - Bad signature → 401, processor untouched.
 *   - Missing LiveKit creds → 503.
 *   - Verified event → processor invoked with the event, 200 response.
 *   - Processor throw → 500 (LiveKit will retry).
 *
 * Mocks the verifier + processor so we don't pull the livekit-server-sdk
 * (and its native crypto deps) into the unit-test runtime.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

// `vi.hoisted` lifts these mock fns so they're available inside the
// `vi.mock` factories below (mock factories run before module-level code).
const mocks = vi.hoisted(() => ({
  verifyWebhookEvent: vi.fn(),
  processEgressWebhookEvent: vi.fn(),
}));

class FakeLiveKitWebhookNotConfiguredError extends Error {
  readonly missing: readonly string[];
  constructor(missing: readonly string[]) {
    super(`not configured: ${missing.join(',')}`);
    this.name = 'LiveKitWebhookNotConfiguredError';
    this.missing = missing;
  }
}

class FakeWebhookSignatureError extends Error {
  readonly reason: string;
  constructor(reason: string) {
    super(`bad sig: ${reason}`);
    this.name = 'WebhookSignatureError';
    this.reason = reason;
  }
}

vi.mock('@/features/recordings', () => ({
  verifyWebhookEvent: mocks.verifyWebhookEvent,
  processEgressWebhookEvent: mocks.processEgressWebhookEvent,
  defaultWebhookRecordingsRepo: {
    findSessionIdByRoomName: vi.fn(),
    setCompositeRecordingUrl: vi.fn(),
    upsertParticipantRecordingUrl: vi.fn(),
  },
  LiveKitWebhookNotConfiguredError: FakeLiveKitWebhookNotConfiguredError,
  WebhookSignatureError: FakeWebhookSignatureError,
}));

async function importRoute() {
  return import('./route');
}

function webhookRequest(body: string, authHeader: string | null) {
  const headers: Record<string, string> = { 'content-type': 'application/webhook+json' };
  if (authHeader !== null) headers['authorization'] = authHeader;
  return new Request('http://localhost/api/livekit/webhook', {
    method: 'POST',
    headers,
    body,
  });
}

describe('POST /api/livekit/webhook', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 when the signature is invalid', async () => {
    mocks.verifyWebhookEvent.mockRejectedValueOnce(new FakeWebhookSignatureError('bad_hmac'));
    const { POST } = await importRoute();

    const res = await POST(webhookRequest('{"raw":"body"}', 'Bearer wrong-token'));

    expect(res.status).toBe(401);
    const json = (await res.json()) as { error: string };
    expect(json.error).toBe('invalid_signature');
    expect(mocks.processEgressWebhookEvent).not.toHaveBeenCalled();
  });

  it('returns 401 when the authorization header is missing', async () => {
    mocks.verifyWebhookEvent.mockRejectedValueOnce(
      new FakeWebhookSignatureError('missing_authorization_header'),
    );
    const { POST } = await importRoute();

    const res = await POST(webhookRequest('{"raw":"body"}', null));

    expect(res.status).toBe(401);
    expect(mocks.processEgressWebhookEvent).not.toHaveBeenCalled();
  });

  it('returns 503 when LiveKit creds are not configured', async () => {
    mocks.verifyWebhookEvent.mockRejectedValueOnce(
      new FakeLiveKitWebhookNotConfiguredError(['LIVEKIT_API_SECRET']),
    );
    const { POST } = await importRoute();

    const res = await POST(webhookRequest('{}', 'Bearer x'));

    expect(res.status).toBe(503);
    const json = (await res.json()) as { error: string };
    expect(json.error).toBe('webhook_not_configured');
    expect(mocks.processEgressWebhookEvent).not.toHaveBeenCalled();
  });

  it('returns 200 with the processor result on the happy path', async () => {
    const fakeEvent = {
      event: 'egress_ended',
      id: 'evt-1',
      egressInfo: { egressId: 'EG_X', roomName: 'room-x' },
    };
    mocks.verifyWebhookEvent.mockResolvedValueOnce(fakeEvent);
    mocks.processEgressWebhookEvent.mockResolvedValueOnce({
      kind: 'persisted_composite',
      sessionId: 'sess-1',
      r2Key: 'sessions/sess-1/composite.mp4',
    });
    const { POST } = await importRoute();

    const res = await POST(webhookRequest('{"event":"egress_ended"}', 'Bearer good'));

    expect(res.status).toBe(200);
    const json = (await res.json()) as {
      ok: boolean;
      result: { kind: string; sessionId: string };
    };
    expect(json.ok).toBe(true);
    expect(json.result.kind).toBe('persisted_composite');
    expect(json.result.sessionId).toBe('sess-1');

    expect(mocks.verifyWebhookEvent).toHaveBeenCalledTimes(1);
    expect(mocks.verifyWebhookEvent).toHaveBeenCalledWith(
      '{"event":"egress_ended"}',
      'Bearer good',
    );
    expect(mocks.processEgressWebhookEvent).toHaveBeenCalledTimes(1);
    expect(mocks.processEgressWebhookEvent).toHaveBeenCalledWith(
      fakeEvent,
      expect.objectContaining({ repo: expect.any(Object) }),
    );
  });

  it('returns 500 when the processor throws (LiveKit retries)', async () => {
    mocks.verifyWebhookEvent.mockResolvedValueOnce({
      event: 'egress_ended',
      id: 'evt-2',
      egressInfo: { egressId: 'EG_Y' },
    });
    mocks.processEgressWebhookEvent.mockRejectedValueOnce(new Error('db_unreachable'));
    const { POST } = await importRoute();

    const res = await POST(webhookRequest('{}', 'Bearer good'));

    expect(res.status).toBe(500);
    const json = (await res.json()) as { error: string };
    expect(json.error).toBe('processor_error');
  });

  it('still returns 200 when the processor reports an unknown room', async () => {
    mocks.verifyWebhookEvent.mockResolvedValueOnce({
      event: 'egress_ended',
      id: 'evt-3',
      egressInfo: { egressId: 'EG_Z', roomName: 'room-deleted' },
    });
    mocks.processEgressWebhookEvent.mockResolvedValueOnce({
      kind: 'unknown_room',
      roomName: 'room-deleted',
    });
    const { POST } = await importRoute();

    const res = await POST(webhookRequest('{}', 'Bearer good'));

    expect(res.status).toBe(200);
    const json = (await res.json()) as { ok: boolean; result: { kind: string } };
    expect(json.ok).toBe(true);
    expect(json.result.kind).toBe('unknown_room');
  });
});
