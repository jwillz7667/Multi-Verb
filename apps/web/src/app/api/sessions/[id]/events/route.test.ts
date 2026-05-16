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
const listStateSnapshotsSinceMock =
  vi.fn<(id: string, opts: { afterSnapshotId?: string; limit?: number }) => Promise<unknown[]>>();
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
    listStateSnapshotsSince: listStateSnapshotsSinceMock,
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
    listStateSnapshotsSinceMock.mockResolvedValue([]);
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

  it('passes Last-Event-ID through to both backfill queries', async () => {
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
    listStateSnapshotsSinceMock.mockResolvedValue([]);
    createSubscriberMock.mockReturnValue(buildFakeSubscriber());

    const { GET } = await importRoute();
    const res = await GET(
      new Request('http://localhost/api/sessions/sess-2/events', {
        headers: { 'last-event-id': 'cursor-99' },
      }),
      makeContext('sess-2'),
    );

    // Drain enough chunks so the start handler finishes the backfill
    // step (it Promise.all's both repos, then writes events).
    const reader = res.body!.getReader();
    await reader.read();
    // A second read picks up post-backfill frames; if no further frames
    // are emitted within ~10ms the test still cancels cleanly.
    const racedRead = await Promise.race([
      reader.read(),
      new Promise<undefined>((resolve) => {
        setTimeout(() => {
          resolve(undefined);
        }, 10);
      }),
    ]);
    void racedRead;

    // The same Last-Event-ID is offered to both repos; each silently
    // ignores it if the row doesn't belong to that table/session.
    expect(listUtterancesSinceMock).toHaveBeenCalledWith('sess-2', {
      afterUtteranceId: 'cursor-99',
      limit: 500,
    });
    expect(listStateSnapshotsSinceMock).toHaveBeenCalledWith('sess-2', {
      afterSnapshotId: 'cursor-99',
      limit: 240,
    });

    await reader.cancel();
  });

  it('routes a published state_snapshot envelope as event: state_snapshot', async () => {
    authMock.mockResolvedValue({ user: { email: 'r@example.com' } });
    findSessionByIdMock.mockResolvedValue({
      id: 'sess-3',
      livekitRoomName: 'room-z',
      status: 'live',
      scheduledStart: null,
      actualStart: new Date(),
      actualEnd: null,
      createdAt: new Date(),
    });
    listUtterancesSinceMock.mockResolvedValue([]);
    listStateSnapshotsSinceMock.mockResolvedValue([]);
    const fake = buildFakeSubscriber();
    createSubscriberMock.mockReturnValue(fake);

    const { GET } = await importRoute();
    const res = await GET(
      new Request('http://localhost/api/sessions/sess-3/events'),
      makeContext('sess-3'),
    );

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();

    // Drain the ready frame.
    await reader.read();

    // Emit a snapshot envelope onto the fake pub/sub channel; the route
    // should re-publish it as `event: state_snapshot`.
    const snapshotEnvelope = {
      type: 'state_snapshot',
      id: '44444444-4444-4444-8444-444444444444',
      session_id: '55555555-5555-4555-8555-555555555555',
      ts: '2026-05-16T12:34:56.789Z',
      payload: {
        snapshot_id: '44444444-4444-4444-8444-444444444444',
        state: {
          session_id: '55555555-5555-4555-8555-555555555555',
          tick_id: 7,
          t: '2026-05-16T12:34:56.500Z',
          started_at: '2026-05-16T12:30:00.000Z',
          scheduled_end_at: null,
          elapsed_sec: 296.5,
          participants: {},
          currently_speaking_count: 0,
          silence_run_sec: 1.2,
          rolling_global_transcript_2min: '',
          is_paused: false,
          moderator_muted: false,
          quietness_budget: {
            current_window_count: 0,
            last_utterance_at: null,
            max_utterances_per_10min: 8,
            min_seconds_between_utterances: 15,
          },
        },
      },
    };
    fake.emit('message', 'verbio:events:sess-3', JSON.stringify(snapshotEnvelope));

    // Drain frames until we find the state_snapshot one (or time out).
    let body = '';
    for (let i = 0; i < 6 && !body.includes('event: state_snapshot'); i++) {
      const next = await Promise.race([
        reader.read(),
        new Promise<{ value: undefined; done: true }>((resolve) => {
          setTimeout(() => {
            resolve({ value: undefined, done: true });
          }, 20);
        }),
      ]);
      if (next.value !== undefined) body += decoder.decode(next.value);
    }

    expect(body).toContain('event: state_snapshot');
    expect(body).toContain('"type":"state_snapshot"');
    expect(body).toContain('44444444-4444-4444-8444-444444444444');

    await reader.cancel();
  });
});
