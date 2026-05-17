/**
 * Recordings feature — public surface.
 *
 * Owns the Cloudflare R2 client used for replay audio and exports.
 * Future layers in Phase 6 add LiveKit egress webhook handling and the
 * replay-side download endpoints; everything that talks to R2 routes
 * through this barrel.
 */

export {
  LiveKitWebhookNotConfiguredError,
  R2KeyInvalidError,
  R2NotConfiguredError,
  WebhookSignatureError,
} from './errors';
export { deleteR2Keys, getR2Client, sessionObjectKey, signGetUrl } from './r2';
export { parseParticipantRecordingUrls } from './replay-urls';
export {
  createPrismaWebhookRecordingsRepo,
  defaultWebhookRecordingsRepo,
  processEgressWebhookEvent,
} from './webhook';
export type {
  IgnoreReason,
  ProcessDeps,
  ProcessLogger,
  ProcessResult,
  WebhookRecordingsRepo,
} from './webhook';
export { getWebhookReceiver, verifyWebhookEvent } from './webhook-receiver';
