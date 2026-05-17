/**
 * Unit tests for the R2 client + signed-URL helper.
 *
 * Strategy: mock `@/lib/env` to control which env vars are present, and
 * mock `@aws-sdk/s3-request-presigner` so we can observe the `Bucket` /
 * `Key` / `expiresIn` arguments without touching network. The
 * `S3Client` constructor itself is real but never makes a request when
 * only the presigner is invoked.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { R2KeyInvalidError, R2NotConfiguredError } from './errors';
// `r2` is imported here so its `R2KeyInvalidError` class identity comes
// from the same `./errors` module as the assertion side — without this,
// `vi.resetModules()` would later spawn parallel error classes and
// `toBeInstanceOf` would fail. The `vi.mock` calls below are hoisted
// above this import by vitest, so `@/lib/env` is already replaced by
// the time `r2.ts` reads `serverEnv`.
import * as r2 from './r2';

// `undefined` is in the type union because `exactOptionalPropertyTypes`
// is on — tests need to set individual vars to `undefined` to exercise
// the missing-config branches.
interface MockServerEnv {
  R2_ACCOUNT_ID?: string | undefined;
  R2_ACCESS_KEY_ID?: string | undefined;
  R2_SECRET_ACCESS_KEY?: string | undefined;
  R2_BUCKET?: string | undefined;
}

// `vi.hoisted` lifts these alongside the `vi.mock` calls so the
// factories below can close over them without a temporal-dead-zone
// error — the mocks run before any top-level `import` resolves.
const mocks = vi.hoisted(() => ({
  serverEnvHolder: { current: null as MockServerEnv | null },
  getSignedUrlMock:
    vi.fn<
      (
        client: unknown,
        command: { input: { Bucket?: string; Key?: string } },
        options: { expiresIn: number },
      ) => Promise<string>
    >(),
}));

vi.mock('@/lib/env', () => ({
  get serverEnv() {
    return mocks.serverEnvHolder.current;
  },
  get env() {
    return mocks.serverEnvHolder.current ?? {};
  },
  get clientEnv() {
    return {};
  },
}));

vi.mock('@aws-sdk/s3-request-presigner', () => ({
  getSignedUrl: mocks.getSignedUrlMock,
}));

const { serverEnvHolder, getSignedUrlMock } = mocks;

function importR2(): typeof r2 {
  // Drop the cached S3Client so each test rebuilds it against whatever
  // `serverEnvHolder.current` is set to.
  r2.__resetR2ClientForTests();
  return r2;
}

const FULL_ENV: MockServerEnv = {
  R2_ACCOUNT_ID: 'acct-123',
  R2_ACCESS_KEY_ID: 'ak-test',
  R2_SECRET_ACCESS_KEY: 'sk-test',
  R2_BUCKET: 'verbio-recordings',
};

beforeEach(() => {
  getSignedUrlMock.mockReset();
  getSignedUrlMock.mockResolvedValue('https://r2.signed.example/abc');
  serverEnvHolder.current = { ...FULL_ENV };
});

describe('signGetUrl', () => {
  it('passes the bucket + key + TTL through to the presigner', async () => {
    const { signGetUrl } = importR2();

    const url = await signGetUrl('sessions/sid/recording.mp4', 120);

    expect(url).toBe('https://r2.signed.example/abc');
    expect(getSignedUrlMock).toHaveBeenCalledTimes(1);
    const [, command, options] = getSignedUrlMock.mock.calls[0]!;
    expect(command.input.Bucket).toBe('verbio-recordings');
    expect(command.input.Key).toBe('sessions/sid/recording.mp4');
    expect(options.expiresIn).toBe(120);
  });

  it('defaults the TTL to one hour when omitted', async () => {
    const { signGetUrl } = importR2();
    await signGetUrl('sessions/sid/recording.mp4');
    const [, , options] = getSignedUrlMock.mock.calls[0]!;
    expect(options.expiresIn).toBe(60 * 60);
  });

  it('clamps TTL above the SigV4 maximum down to 7 days', async () => {
    const { signGetUrl } = importR2();
    const tenDays = 60 * 60 * 24 * 10;
    await signGetUrl('sessions/sid/recording.mp4', tenDays);
    const [, , options] = getSignedUrlMock.mock.calls[0]!;
    expect(options.expiresIn).toBe(60 * 60 * 24 * 7);
  });

  it('clamps TTL below 1 second up to 1', async () => {
    const { signGetUrl } = importR2();
    await signGetUrl('sessions/sid/recording.mp4', 0);
    const [, , options] = getSignedUrlMock.mock.calls[0]!;
    expect(options.expiresIn).toBe(1);
  });

  it('rejects an empty key without calling the presigner', async () => {
    const { signGetUrl } = importR2();
    await expect(signGetUrl('')).rejects.toBeInstanceOf(R2KeyInvalidError);
    expect(getSignedUrlMock).not.toHaveBeenCalled();
  });

  it('rejects a key with a leading slash', async () => {
    const { signGetUrl } = importR2();
    await expect(signGetUrl('/leading-slash.mp4')).rejects.toBeInstanceOf(R2KeyInvalidError);
    expect(getSignedUrlMock).not.toHaveBeenCalled();
  });

  it.each([
    ['R2_ACCOUNT_ID', { R2_ACCOUNT_ID: undefined }],
    ['R2_ACCESS_KEY_ID', { R2_ACCESS_KEY_ID: undefined }],
    ['R2_SECRET_ACCESS_KEY', { R2_SECRET_ACCESS_KEY: undefined }],
    ['R2_BUCKET', { R2_BUCKET: undefined }],
  ])('raises R2NotConfiguredError when %s is missing', async (missing, patch) => {
    serverEnvHolder.current = { ...FULL_ENV, ...patch };
    const { signGetUrl } = importR2();
    try {
      await signGetUrl('sessions/sid/recording.mp4');
      throw new Error('expected R2NotConfiguredError');
    } catch (err) {
      expect(err).toBeInstanceOf(R2NotConfiguredError);
      expect((err as R2NotConfiguredError).missing).toContain(missing);
    }
    expect(getSignedUrlMock).not.toHaveBeenCalled();
  });

  it('raises R2NotConfiguredError when serverEnv is null (client context)', async () => {
    serverEnvHolder.current = null;
    const { signGetUrl } = importR2();
    await expect(signGetUrl('sessions/sid/recording.mp4')).rejects.toBeInstanceOf(
      R2NotConfiguredError,
    );
  });
});

describe('getR2Client', () => {
  it('reuses the same S3Client across calls (singleton)', () => {
    const { getR2Client } = importR2();
    const a = getR2Client();
    const b = getR2Client();
    expect(a.client).toBe(b.client);
    expect(a.bucket).toBe('verbio-recordings');
  });

  it('rebuilds the configuration check on each call (env changes mid-process)', () => {
    const { getR2Client } = importR2();
    getR2Client();
    serverEnvHolder.current = { ...FULL_ENV, R2_BUCKET: undefined };
    expect(() => getR2Client()).toThrow(R2NotConfiguredError);
  });
});

describe('sessionObjectKey', () => {
  it('returns a sessions/{id}/ prefixed key', () => {
    const { sessionObjectKey } = importR2();
    expect(sessionObjectKey('sid-1', 'composite.mp4')).toBe('sessions/sid-1/composite.mp4');
    expect(sessionObjectKey('sid-1', 'tracks', 'p-1.opus')).toBe('sessions/sid-1/tracks/p-1.opus');
  });

  it('rejects empty session id', () => {
    const { sessionObjectKey } = importR2();
    expect(() => sessionObjectKey('', 'composite.mp4')).toThrow(R2KeyInvalidError);
  });

  it('rejects empty parts', () => {
    const { sessionObjectKey } = importR2();
    expect(() => sessionObjectKey('sid-1', '')).toThrow(R2KeyInvalidError);
  });

  it('rejects path traversal in parts', () => {
    const { sessionObjectKey } = importR2();
    expect(() => sessionObjectKey('sid-1', '..', 'other.mp4')).toThrow(R2KeyInvalidError);
    expect(() => sessionObjectKey('sid-1', 'tracks/../other')).toThrow(R2KeyInvalidError);
  });

  it('rejects leading slash in parts', () => {
    const { sessionObjectKey } = importR2();
    expect(() => sessionObjectKey('sid-1', '/composite.mp4')).toThrow(R2KeyInvalidError);
  });
});
