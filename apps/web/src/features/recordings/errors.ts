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
