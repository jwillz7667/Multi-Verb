/**
 * Cloudflare R2 client + signed-URL helper.
 *
 * R2 is S3-protocol-compatible, so we use `@aws-sdk/client-s3` with an
 * R2-specific endpoint (`https://{account_id}.r2.cloudflarestorage.com`)
 * and the fixed region literal `"auto"`. Credentials are scoped R2
 * tokens (Access Key ID + Secret) issued from the Cloudflare dashboard
 * — they're S3-shaped but only authorize the configured bucket(s).
 *
 * The signed-URL path uses `@aws-sdk/s3-request-presigner` to mint
 * short-lived GET URLs for replay audio and exports. Web routes hand
 * these out to authenticated researchers with a default 1-hour TTL;
 * the upstream auth check is the only thing standing between the URL
 * and the recording, so TTLs should stay short.
 *
 * Hot-reload safety: stash the singleton on `globalThis` (matches the
 * Redis pattern in `lib/redis.ts`) so Next.js dev mode doesn't leak a
 * client per file change.
 *
 * Why a lazy singleton rather than an eager module-level instance:
 * R2 env vars are `.optional()` in the env schema (the engine boots
 * fine without R2 configured for tests + early dev). Constructing the
 * client at import time would force every web route to fail on a
 * missing R2 bucket. Instead we throw `R2NotConfiguredError` only when
 * a caller actually tries to use R2.
 */

import 'server-only';

import { GetObjectCommand, S3Client } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';

import { serverEnv } from '@/lib/env';

import { R2KeyInvalidError, R2NotConfiguredError } from './errors';

declare global {
  var __verbioR2Client: S3Client | undefined;
}

const R2_REGION = 'auto';
const DEFAULT_GET_URL_TTL_SECONDS = 60 * 60;
const MAX_GET_URL_TTL_SECONDS = 60 * 60 * 24 * 7; // 7d — AWS SigV4 hard cap.

interface R2Config {
  accountId: string;
  accessKeyId: string;
  secretAccessKey: string;
  bucket: string;
}

function readConfig(): R2Config {
  if (serverEnv === null) {
    throw new R2NotConfiguredError(['<server context unavailable>']);
  }
  const { R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET } = serverEnv;
  const missing: string[] = [];
  if (R2_ACCOUNT_ID === undefined) missing.push('R2_ACCOUNT_ID');
  if (R2_ACCESS_KEY_ID === undefined) missing.push('R2_ACCESS_KEY_ID');
  if (R2_SECRET_ACCESS_KEY === undefined) missing.push('R2_SECRET_ACCESS_KEY');
  if (R2_BUCKET === undefined) missing.push('R2_BUCKET');
  // The redundant per-field check after the missing-list collection is
  // what narrows each value from `string | undefined` to `string` —
  // the linter forbids `!` assertions and a generic `missing.length > 0`
  // throw doesn't propagate that narrowing back to the destructured
  // bindings.
  if (
    R2_ACCOUNT_ID === undefined ||
    R2_ACCESS_KEY_ID === undefined ||
    R2_SECRET_ACCESS_KEY === undefined ||
    R2_BUCKET === undefined
  ) {
    throw new R2NotConfiguredError(missing);
  }
  return {
    accountId: R2_ACCOUNT_ID,
    accessKeyId: R2_ACCESS_KEY_ID,
    secretAccessKey: R2_SECRET_ACCESS_KEY,
    bucket: R2_BUCKET,
  };
}

function buildClient(config: R2Config): S3Client {
  return new S3Client({
    region: R2_REGION,
    endpoint: `https://${config.accountId}.r2.cloudflarestorage.com`,
    credentials: {
      accessKeyId: config.accessKeyId,
      secretAccessKey: config.secretAccessKey,
    },
    // Force path-style addressing — virtual-hosted-style on R2 requires
    // a custom domain that the bucket has been published under. We
    // don't assume that, so always go through the account endpoint.
    forcePathStyle: true,
  });
}

/**
 * Return the lazy R2 client. Caller is responsible for handling
 * `R2NotConfiguredError` at the boundary.
 *
 * Exported for tests; route code should prefer `signGetUrl()`.
 */
export function getR2Client(): { client: S3Client; bucket: string } {
  if (globalThis.__verbioR2Client === undefined) {
    const config = readConfig();
    globalThis.__verbioR2Client = buildClient(config);
    return { client: globalThis.__verbioR2Client, bucket: config.bucket };
  }
  // The bucket comes from env, not the client instance, so re-read it.
  // `readConfig()` will throw the same `R2NotConfiguredError` if env
  // disappeared between calls (e.g. hot reload in dev).
  const config = readConfig();
  return { client: globalThis.__verbioR2Client, bucket: config.bucket };
}

/**
 * Mint a short-lived GET URL for an R2 object.
 *
 * `ttlSeconds` is clamped to AWS's SigV4 maximum (7 days). The default
 * (1 hour) is right for in-page audio playback. Exports that produce a
 * download link the researcher might keep open longer should pass a
 * larger TTL explicitly — but never longer than the user's tolerance
 * for "the link expired" UX. Refresh-via-server-route is the better
 * pattern for anything beyond a few hours.
 *
 * Throws `R2NotConfiguredError` if env is missing.
 * Throws `R2KeyInvalidError` if `key` is empty or starts with `/`.
 */
export async function signGetUrl(
  key: string,
  ttlSeconds: number = DEFAULT_GET_URL_TTL_SECONDS,
): Promise<string> {
  if (key.length === 0) {
    throw new R2KeyInvalidError('R2 key must not be empty');
  }
  if (key.startsWith('/')) {
    throw new R2KeyInvalidError(`R2 key must not start with '/': ${key}`);
  }
  const ttl = Math.max(1, Math.min(ttlSeconds, MAX_GET_URL_TTL_SECONDS));
  const { client, bucket } = getR2Client();
  const command = new GetObjectCommand({ Bucket: bucket, Key: key });
  return getSignedUrl(client, command, { expiresIn: ttl });
}

/**
 * Construct a canonical R2 key for a session-scoped artifact.
 *
 * All recording/export keys are namespaced under `sessions/<uuid>/...`
 * so a per-session retention sweep can list-and-delete with a single
 * prefix. Centralising the layout here keeps egress, exports, and
 * retention in agreement.
 *
 * Throws `R2KeyInvalidError` on empty session id or relative `parts`
 * that would break the prefix (`..`, leading `/`).
 */
export function sessionObjectKey(sessionId: string, ...parts: readonly string[]): string {
  if (sessionId.length === 0) {
    throw new R2KeyInvalidError('sessionId must not be empty');
  }
  for (const part of parts) {
    if (part.length === 0) {
      throw new R2KeyInvalidError('R2 key parts must not be empty');
    }
    if (part.startsWith('/') || part.includes('..')) {
      throw new R2KeyInvalidError(`R2 key part must be relative and not contain '..': ${part}`);
    }
  }
  return ['sessions', sessionId, ...parts].join('/');
}

/**
 * Reset the singleton. Tests only — production never needs to drop
 * the client.
 */
export function __resetR2ClientForTests(): void {
  globalThis.__verbioR2Client = undefined;
}
