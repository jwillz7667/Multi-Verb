/**
 * Typed errors for the recordings feature.
 *
 * Each error subclass carries the missing env var name (or other field)
 * so the boundary handler can surface an actionable message to ops
 * without leaking secrets.
 */

export class R2NotConfiguredError extends Error {
  readonly missing: readonly string[];

  constructor(missing: readonly string[]) {
    super(
      `R2 storage is not configured. Missing env vars: ${missing.join(', ')}. ` +
        `Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and R2_BUCKET ` +
        `to enable recording egress and signed-URL minting.`,
    );
    this.name = 'R2NotConfiguredError';
    this.missing = missing;
  }
}

export class R2KeyInvalidError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'R2KeyInvalidError';
  }
}

/**
 * Raised when the LiveKit webhook endpoint is reachable but the API
 * credentials needed to verify signatures aren't configured. The route
 * surfaces this as 503 so LiveKit's webhook delivery layer retries —
 * a missing secret is an ops-config issue, not a permanent rejection.
 */
export class LiveKitWebhookNotConfiguredError extends Error {
  readonly missing: readonly string[];

  constructor(missing: readonly string[]) {
    super(
      `LiveKit webhook receiver is not configured. Missing env vars: ${missing.join(', ')}. ` +
        `Set LIVEKIT_API_KEY and LIVEKIT_API_SECRET to verify inbound webhook signatures.`,
    );
    this.name = 'LiveKitWebhookNotConfiguredError';
    this.missing = missing;
  }
}

/**
 * Raised when the inbound webhook body fails HMAC verification. The
 * route surfaces this as 401 so the caller knows the signature didn't
 * line up — most often a stale or wrong `LIVEKIT_API_SECRET`. We don't
 * leak verifier details in the public response body.
 */
export class WebhookSignatureError extends Error {
  readonly reason: string;

  constructor(reason: string) {
    super(`LiveKit webhook signature verification failed: ${reason}`);
    this.name = 'WebhookSignatureError';
    this.reason = reason;
  }
}
