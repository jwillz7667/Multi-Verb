/**
 * Boundary tests for the SSE transcript route.
 *
 * The fully-wired streaming path (subscribe → backfill → drain →
 * heartbeat) is exercised end-to-end by the L6 Playwright E2E with
 * fake participants. These vitest cases pin only the boundary
 * contracts that don't need a live Redis: auth gating, 404, and the
 * SSE response headers.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { EventEmitter } from 'node:events';

interface MockSession {
  user?: { email: string } | null;
}

interface FakeSessionRow {
  id: string;
  livekitRoomName: string;
  status: string;
  scheduledStart: Date | null;
  actualStart: Date | null;
  actualEnd: Date | null;
  createdAt: Date;
}

interface FakeSubscriber {
  on: ReturnType<typeof vi.fn>;
  off: ReturnType<typeof vi.fn>;
  subscribe: ReturnType<typeof vi.fn>;
  unsubscribe: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
  emit: EventEmitter['emit'];
}

const findSessionByIdMock = vi.fn<(id: string) => Promise<FakeSessionRow | null>>();
const listUtterancesSinceMock =
  vi.fn<(id: string, opts: { afterUtteranceId?: string; limit?: number }) => Promise<unknown[]>>();
const authMock = vi.fn<() => Promise<MockSession | null>>();
const createSubscriberMock = vi.fn<() => FakeSubscriber>();

vi.mock('@/lib/auth', () => ({
  auth: (): Promise<MockSession | null> => authMock(),
}));

vi.mock('@/features/sessions', async () => {
  const events = await import('@/features/sessions/events');
  return {
    findSessionById: findSessionByIdMock,
    listUtterancesSince: listUtterancesSinceMock,
    parseTranscriptEvent: events.parseTranscriptEvent,
  };
});

vi.mock('@/lib/redis', () => ({
  createSubscriber: createSubscriberMock,
  eventsChannel: (id: string): string => `verbio:events:${id}`,
}));

function buildFakeSubscriber(): FakeSubscriber {
  const listeners: ((channel: string, payload: string) => void)[] = [];
  return {
    on: vi.fn((event: string, cb: (channel: string, payload: string) => void) => {
      if (event === 'message') listeners.push(cb);
    }),
    off: vi.fn((event: string, cb: (channel: string, payload: string) => void) => {
      if (event !== 'message') return;
      const idx = listeners.indexOf(cb);
      if (idx >= 0) listeners.splice(idx, 1);
    }),
    subscribe: vi.fn().mockResolvedValue(undefined),
    unsubscribe: vi.fn().mockResolvedValue(undefined),
    disconnect: vi.fn(),
    emit: ((event: string | symbol, ...args: unknown[]): boolean => {
      if (event !== 'message') return false;
      const [channel, payload] = args as [string, string];
      for (const cb of listeners) cb(channel, payload);
      return true;
    }) as EventEmitter['emit'],
  };
}

async function importRoute() {
  return import('./route');
}

function makeContext(id: string) {
  return { params: Promise.resolve({ id }) };
}

describe('GET /api/sessions/[id]/events', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns 401 for an unauthenticated request', async () => {
    authMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/abc/events'),
      makeContext('abc'),
    );

    expect(res.status).toBe(401);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('unauthorized');
  });

  it('returns 404 when the session is unknown', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionByIdMock.mockResolvedValue(null);
    const { GET } = await importRoute();

    const res = await GET(
      new Request('http://localhost/api/sessions/missing/events'),
      makeContext('missing'),
    );

    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe('session_not_found');
  });

  it('returns SSE headers and a ready frame on successful connect', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionByIdMock.mockResolvedValue({
      id: 'sess-1',
      livekitRoomName: 'room-x',
      status: 'live',
      scheduledStart: null,
      actualStart: new Date(),
      actualEnd: null,
      createdAt: new Date(),
    });
    listUtterancesSinceMock.mockResolvedValue([]);
    const fake = buildFakeSubscriber();
    createSubscriberMock.mockReturnValue(fake);

    const { GET } = await importRoute();
    const res = await GET(
      new Request('http://localhost/api/sessions/sess-1/events'),
      makeContext('sess-1'),
    );

    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('text/event-stream');
    expect(res.headers.get('cache-control')).toBe('no-cache, no-transform');
    expect(res.headers.get('x-accel-buffering')).toBe('no');
    expect(fake.subscribe).toHaveBeenCalledWith('verbio:events:sess-1');

    // Read one chunk to confirm a ready frame is emitted before we
    // cancel; otherwise the stream may never flush its start handler.
    const reader = res.body!.getReader();
    const { value } = await reader.read();
    const text = new TextDecoder().decode(value);
    expect(text).toContain('event: ready');
    expect(text).toContain('"session_id":"sess-1"');

    // cancel() runs the ReadableStream cancel hook → tears down the
    // subscriber + heartbeat interval so the test doesn't leak timers.
    await reader.cancel();
    expect(fake.unsubscribe).toHaveBeenCalledWith('verbio:events:sess-1');
    expect(fake.disconnect).toHaveBeenCalled();
  });

  it('passes Last-Event-ID through to the backfill query', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionByIdMock.mockResolvedValue({
      id: 'sess-2',
      livekitRoomName: 'room-y',
      status: 'live',
      scheduledStart: null,
      actualStart: new Date(),
      actualEnd: null,
      createdAt: new Date(),
    });
    listUtterancesSinceMock.mockResolvedValue([]);
    createSubscriberMock.mockReturnValue(buildFakeSubscriber());

    const { GET } = await importRoute();
    const res = await GET(
      new Request('http://localhost/api/sessions/sess-2/events', {
        headers: { 'last-event-id': 'utt-99' },
      }),
      makeContext('sess-2'),
    );

    // Drain at least one chunk so the start handler runs the backfill.
    const reader = res.body!.getReader();
    await reader.read();

    expect(listUtterancesSinceMock).toHaveBeenCalledWith('sess-2', {
      afterUtteranceId: 'utt-99',
      limit: 500,
    });

    await reader.cancel();
  });
});
