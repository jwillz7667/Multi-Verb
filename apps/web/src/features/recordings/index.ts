/**
 * Recordings feature — public surface.
 *
 * Owns the Cloudflare R2 client used for replay audio and exports.
 * Future layers in Phase 6 add LiveKit egress webhook handling and the
 * replay-side download endpoints; everything that talks to R2 routes
 * through this barrel.
 */

export { R2KeyInvalidError, R2NotConfiguredError } from './errors';
export { getR2Client, sessionObjectKey, signGetUrl } from './r2';
