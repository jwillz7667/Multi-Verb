/**
 * Unit tests for `processEgressWebhookEvent`.
 *
 * The processor is pure given the injected `WebhookRecordingsRepo`, so
 * we substitute an in-memory fake that records every call. Each test
 * exercises one branch of the (event type × egress kind × status) cube
 * and pins the wire-shape `ProcessResult` the route handler hands back.
 *
 * What's NOT tested here:
 *   - The Prisma-backed repo's atomic `$executeRaw` jsonb merge — that's
 *     an integration concern (real Postgres) and lives in the P6
 *     integration suite.
 *   - Signature verification — exercised in route-level tests.
 */

import { describe, expect, it, vi } from 'vitest';

import {
  processEgressWebhookEvent,
  type ProcessResult,
  type WebhookRecordingsRepo,
} from './webhook';

import type { WebhookEvent } from 'livekit-server-sdk';

// EgressStatus enum values mirrored from the @livekit/protocol proto.
const EGRESS_STATUS_ACTIVE = 1;
const EGRESS_STATUS_COMPLETE = 3;
const EGRESS_STATUS_FAILED = 4;
const EGRESS_STATUS_ABORTED = 5;
const EGRESS_STATUS_LIMIT_REACHED = 6;

const SESSION_UUID = '11111111-1111-4111-8111-111111111111';
const ROOM_NAME = 'room-cafe1234deadbeef';

interface FakeRepoSpies {
  repo: WebhookRecordingsRepo;
  findSessionIdByRoomName: ReturnType<typeof vi.fn>;
  setCompositeRecordingUrl: ReturnType<typeof vi.fn>;
  upsertParticipantRecordingUrl: ReturnType<typeof vi.fn>;
}

function makeRepo(overrides: { sessionId?: string | null } = {}): FakeRepoSpies {
  const sessionIdResult = overrides.sessionId === undefined ? SESSION_UUID : overrides.sessionId;
  const findSessionIdByRoomName = vi
    .fn<(roomName: string) => Promise<string | null>>()
    .mockResolvedValue(sessionIdResult);
  const setCompositeRecordingUrl = vi
    .fn<(id: string, key: string) => Promise<void>>()
    .mockResolvedValue(undefined);
  const upsertParticipantRecordingUrl = vi
    .fn<(id: string, identity: string, key: string) => Promise<void>>()
    .mockResolvedValue(undefined);
  const repo: WebhookRecordingsRepo = {
    findSessionIdByRoomName,
    setCompositeRecordingUrl,
    upsertParticipantRecordingUrl,
  };
  return {
    repo,
    findSessionIdByRoomName,
    setCompositeRecordingUrl,
    upsertParticipantRecordingUrl,
  };
}

function silentLogger() {
  return {
    info: vi.fn<(msg: string, ctx?: Record<string, unknown>) => void>(),
    warn: vi.fn<(msg: string, ctx?: Record<string, unknown>) => void>(),
    debug: vi.fn<(msg: string, ctx?: Record<string, unknown>) => void>(),
  };
}

interface EventFixtureOpts {
  event: string;
  status?: number;
  requestCase?: 'roomComposite' | 'participant' | 'web' | 'track' | undefined;
  identity?: string;
  filename?: string | null;
  roomName?: string;
  egressInfoMissing?: boolean;
  error?: string;
}

/**
 * Construct a minimal WebhookEvent that satisfies the processor's
 * structural reads. The processor only touches `event.event`,
 * `event.id`, and `event.egressInfo.*` — we leave the rest empty.
 */
function makeEvent(opts: EventFixtureOpts): WebhookEvent {
  const file =
    opts.filename === null ? undefined : { filename: opts.filename ?? 'sessions/x/composite.mp4' };
  const fileResults = file === undefined ? [] : [file];
  const requestField =
    opts.requestCase === undefined
      ? { case: undefined, value: undefined }
      : opts.requestCase === 'participant'
        ? { case: 'participant', value: { identity: opts.identity ?? 'alice' } }
        : opts.requestCase === 'roomComposite'
          ? { case: 'roomComposite', value: { roomName: opts.roomName ?? ROOM_NAME } }
          : { case: opts.requestCase, value: {} };
  const egressInfo = opts.egressInfoMissing
    ? undefined
    : {
        egressId: 'EG_TEST_0001',
        roomName: opts.roomName ?? ROOM_NAME,
        status: opts.status ?? EGRESS_STATUS_COMPLETE,
        request: requestField,
        fileResults,
        error: opts.error ?? '',
        errorCode: 0,
      };
  return {
    event: opts.event,
    id: 'evt-test-1',
    egressInfo,
  } as unknown as WebhookEvent;
}

describe('processEgressWebhookEvent', () => {
  it('ignores non-egress events without touching the repo', async () => {
    const { repo, findSessionIdByRoomName } = makeRepo();
    const result = await processEgressWebhookEvent(makeEvent({ event: 'room_started' }), {
      repo,
      logger: silentLogger(),
    });

    expect(result).toEqual<ProcessResult>({
      kind: 'ignored',
      reason: 'non_egress_event',
      event: 'room_started',
    });
    expect(findSessionIdByRoomName).not.toHaveBeenCalled();
  });

  it('ignores egress_started without persisting', async () => {
    const { repo, setCompositeRecordingUrl } = makeRepo();
    const result = await processEgressWebhookEvent(
      makeEvent({
        event: 'egress_started',
        status: EGRESS_STATUS_ACTIVE,
        requestCase: 'roomComposite',
      }),
      { repo, logger: silentLogger() },
    );

    expect(result.kind).toBe('ignored');
    if (result.kind !== 'ignored') throw new Error('unreachable');
    expect(result.reason).toBe('non_terminal_egress_event');
    expect(setCompositeRecordingUrl).not.toHaveBeenCalled();
  });

  it('ignores egress_updated without persisting', async () => {
    const { repo, setCompositeRecordingUrl } = makeRepo();
    const result = await processEgressWebhookEvent(
      makeEvent({
        event: 'egress_updated',
        status: EGRESS_STATUS_ACTIVE,
        requestCase: 'participant',
      }),
      { repo, logger: silentLogger() },
    );

    expect(result.kind).toBe('ignored');
    expect(setCompositeRecordingUrl).not.toHaveBeenCalled();
  });

  it('returns `ignored` when egressInfo is missing on egress_ended', async () => {
    const { repo, findSessionIdByRoomName } = makeRepo();
    const logger = silentLogger();
    const result = await processEgressWebhookEvent(
      makeEvent({ event: 'egress_ended', egressInfoMissing: true }),
      { repo, logger },
    );

    expect(result).toEqual<ProcessResult>({
      kind: 'ignored',
      reason: 'missing_egress_info',
      event: 'egress_ended',
    });
    expect(findSessionIdByRoomName).not.toHaveBeenCalled();
    expect(logger.warn).toHaveBeenCalledWith(
      'livekit_webhook.missing_egress_info',
      expect.objectContaining({ event: 'egress_ended' }),
    );
  });

  it('returns `unknown_room` when no session row matches the room name', async () => {
    const { repo, setCompositeRecordingUrl, upsertParticipantRecordingUrl } = makeRepo({
      sessionId: null,
    });
    const logger = silentLogger();
    const result = await processEgressWebhookEvent(
      makeEvent({
        event: 'egress_ended',
        status: EGRESS_STATUS_COMPLETE,
        requestCase: 'roomComposite',
      }),
      { repo, logger },
    );

    expect(result).toEqual<ProcessResult>({
      kind: 'unknown_room',
      roomName: ROOM_NAME,
    });
    expect(setCompositeRecordingUrl).not.toHaveBeenCalled();
    expect(upsertParticipantRecordingUrl).not.toHaveBeenCalled();
    expect(logger.warn).toHaveBeenCalledWith(
      'livekit_webhook.unknown_room',
      expect.objectContaining({ roomName: ROOM_NAME }),
    );
  });

  it('persists composite recording URL and returns the typed outcome', async () => {
    const { repo, setCompositeRecordingUrl, upsertParticipantRecordingUrl } = makeRepo();
    const result = await processEgressWebhookEvent(
      makeEvent({
        event: 'egress_ended',
        status: EGRESS_STATUS_COMPLETE,
        requestCase: 'roomComposite',
        filename: `sessions/${SESSION_UUID}/composite.mp4`,
      }),
      { repo, logger: silentLogger() },
    );

    expect(result).toEqual<ProcessResult>({
      kind: 'persisted_composite',
      sessionId: SESSION_UUID,
      r2Key: `sessions/${SESSION_UUID}/composite.mp4`,
    });
    expect(setCompositeRecordingUrl).toHaveBeenCalledTimes(1);
    expect(setCompositeRecordingUrl).toHaveBeenCalledWith(
      SESSION_UUID,
      `sessions/${SESSION_UUID}/composite.mp4`,
    );
    expect(upsertParticipantRecordingUrl).not.toHaveBeenCalled();
  });

  it('persists participant recording URL keyed by identity', async () => {
    const { repo, upsertParticipantRecordingUrl, setCompositeRecordingUrl } = makeRepo();
    const result = await processEgressWebhookEvent(
      makeEvent({
        event: 'egress_ended',
        status: EGRESS_STATUS_COMPLETE,
        requestCase: 'participant',
        identity: 'p-maya-42',
        filename: `sessions/${SESSION_UUID}/tracks/p-maya-42.ogg`,
      }),
      { repo, logger: silentLogger() },
    );

    expect(result).toEqual<ProcessResult>({
      kind: 'persisted_participant',
      sessionId: SESSION_UUID,
      identity: 'p-maya-42',
      r2Key: `sessions/${SESSION_UUID}/tracks/p-maya-42.ogg`,
    });
    expect(upsertParticipantRecordingUrl).toHaveBeenCalledTimes(1);
    expect(upsertParticipantRecordingUrl).toHaveBeenCalledWith(
      SESSION_UUID,
      'p-maya-42',
      `sessions/${SESSION_UUID}/tracks/p-maya-42.ogg`,
    );
    expect(setCompositeRecordingUrl).not.toHaveBeenCalled();
  });

  it.each([
    ['EGRESS_FAILED', EGRESS_STATUS_FAILED],
    ['EGRESS_ABORTED', EGRESS_STATUS_ABORTED],
    ['EGRESS_LIMIT_REACHED', EGRESS_STATUS_LIMIT_REACHED],
  ])('logs egress_failed and persists nothing when status is %s', async (_label, status) => {
    const { repo, setCompositeRecordingUrl, upsertParticipantRecordingUrl } = makeRepo();
    const logger = silentLogger();
    const result = await processEgressWebhookEvent(
      makeEvent({
        event: 'egress_ended',
        status,
        requestCase: 'roomComposite',
        error: 'recording_failed_intentional_in_test',
      }),
      { repo, logger },
    );

    expect(result).toEqual<ProcessResult>({
      kind: 'egress_failed',
      sessionId: SESSION_UUID,
      egressKind: 'roomComposite',
      status,
      error: 'recording_failed_intentional_in_test',
    });
    expect(setCompositeRecordingUrl).not.toHaveBeenCalled();
    expect(upsertParticipantRecordingUrl).not.toHaveBeenCalled();
    expect(logger.warn).toHaveBeenCalledWith(
      'livekit_webhook.egress_failed',
      expect.objectContaining({
        sessionId: SESSION_UUID,
        status,
        egressKind: 'roomComposite',
      }),
    );
  });

  it('ignores unsupported request kinds (e.g., trackComposite, web)', async () => {
    const { repo, setCompositeRecordingUrl, upsertParticipantRecordingUrl } = makeRepo();
    const result = await processEgressWebhookEvent(
      makeEvent({
        event: 'egress_ended',
        status: EGRESS_STATUS_COMPLETE,
        requestCase: 'web',
      }),
      { repo, logger: silentLogger() },
    );

    expect(result.kind).toBe('ignored');
    if (result.kind !== 'ignored') throw new Error('unreachable');
    expect(result.reason).toBe('unsupported_request_kind');
    expect(setCompositeRecordingUrl).not.toHaveBeenCalled();
    expect(upsertParticipantRecordingUrl).not.toHaveBeenCalled();
  });

  it('ignores ended events with no file results', async () => {
    const { repo, setCompositeRecordingUrl } = makeRepo();
    const result = await processEgressWebhookEvent(
      makeEvent({
        event: 'egress_ended',
        status: EGRESS_STATUS_COMPLETE,
        requestCase: 'roomComposite',
        filename: null,
      }),
      { repo, logger: silentLogger() },
    );

    expect(result.kind).toBe('ignored');
    if (result.kind !== 'ignored') throw new Error('unreachable');
    expect(result.reason).toBe('no_file_results');
    expect(setCompositeRecordingUrl).not.toHaveBeenCalled();
  });

  it('ignores participant events with an empty identity', async () => {
    const { repo, upsertParticipantRecordingUrl } = makeRepo();
    const result = await processEgressWebhookEvent(
      makeEvent({
        event: 'egress_ended',
        status: EGRESS_STATUS_COMPLETE,
        requestCase: 'participant',
        identity: '',
        filename: `sessions/${SESSION_UUID}/tracks/unknown.ogg`,
      }),
      { repo, logger: silentLogger() },
    );

    expect(result.kind).toBe('ignored');
    if (result.kind !== 'ignored') throw new Error('unreachable');
    expect(result.reason).toBe('missing_participant_identity');
    expect(upsertParticipantRecordingUrl).not.toHaveBeenCalled();
  });

  it('ignores ended events with a missing room name', async () => {
    const { repo, findSessionIdByRoomName } = makeRepo();
    const result = await processEgressWebhookEvent(
      makeEvent({
        event: 'egress_ended',
        status: EGRESS_STATUS_COMPLETE,
        requestCase: 'roomComposite',
        roomName: '',
      }),
      { repo, logger: silentLogger() },
    );

    expect(result.kind).toBe('ignored');
    if (result.kind !== 'ignored') throw new Error('unreachable');
    expect(result.reason).toBe('missing_room_name');
    expect(findSessionIdByRoomName).not.toHaveBeenCalled();
  });

  it('is idempotent — a duplicate ended event re-issues the same UPDATE', async () => {
    const { repo, setCompositeRecordingUrl } = makeRepo();
    const evt = makeEvent({
      event: 'egress_ended',
      status: EGRESS_STATUS_COMPLETE,
      requestCase: 'roomComposite',
      filename: `sessions/${SESSION_UUID}/composite.mp4`,
    });
    const first = await processEgressWebhookEvent(evt, {
      repo,
      logger: silentLogger(),
    });
    const second = await processEgressWebhookEvent(evt, {
      repo,
      logger: silentLogger(),
    });

    expect(first.kind).toBe('persisted_composite');
    expect(second.kind).toBe('persisted_composite');
    expect(setCompositeRecordingUrl).toHaveBeenCalledTimes(2);
    expect(setCompositeRecordingUrl).toHaveBeenNthCalledWith(
      1,
      SESSION_UUID,
      `sessions/${SESSION_UUID}/composite.mp4`,
    );
    expect(setCompositeRecordingUrl).toHaveBeenNthCalledWith(
      2,
      SESSION_UUID,
      `sessions/${SESSION_UUID}/composite.mp4`,
    );
  });
});
