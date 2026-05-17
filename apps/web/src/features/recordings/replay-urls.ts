/**
 * Replay-side helpers for resolving R2 keys to playback URLs.
 *
 * `sessions.per_participant_recording_urls` is a JSONB column the
 * egress webhook handler writes one identity-keyed entry at a time
 * (Phase 6 L3). Two participants whose egresses finish concurrently
 * compose via `jsonb || jsonb_build_object(...)` atomically, so a row
 * read here might carry between zero and N entries. The shape on disk
 * is intentionally not constrained at the schema level — Postgres
 * JSONB is wide — so we validate the projection here with a strict
 * runtime check.
 *
 * Why a separate module instead of inlining in the page:
 *   - Validation logic is unit-testable in isolation.
 *   - The same parser is reused by the audio route (`GET /api/sessions
 *     /[id]/recordings/audio?participant=…`) and the future
 *     per-participant export endpoint (Phase 6 L13).
 *   - Keeps the page server component focused on layout and data
 *     hydration, not JSON shape policing.
 */

import 'server-only';

/**
 * Normalize the JSONB column into a `{ identity: r2Key }` map.
 *
 * The contract: the value MUST be either `null` (no participant
 * egresses completed yet) or a flat object whose every value is a
 * non-empty string (the R2 object key). Anything else — arrays, nested
 * objects, numeric values — is a corruption signal and is dropped
 * silently here; the caller sees an empty map rather than a crash.
 *
 * Why silent drop rather than throw: the replay UI must remain
 * operable for sessions whose composite recording succeeded but whose
 * per-participant track had a bad row. A throw on read would 500 the
 * page over a single bad entry; an empty map degrades gracefully to
 * "no per-participant audio available".
 */
export function parseParticipantRecordingUrls(value: unknown): Record<string, string> {
  if (value === null || value === undefined) return {};
  if (typeof value !== 'object' || Array.isArray(value)) return {};

  const out: Record<string, string> = {};
  // Object cast is safe after the typeof+Array.isArray guards above —
  // unknown narrows to a record-like shape and we iterate its entries
  // defensively before keeping any.
  for (const [identity, rawKey] of Object.entries(value as Record<string, unknown>)) {
    if (typeof identity !== 'string' || identity.length === 0) continue;
    if (typeof rawKey !== 'string' || rawKey.length === 0) continue;
    // R2 keys must be relative — `signGetUrl` already throws on
    // leading `/`, but rejecting here keeps the rejection at the
    // boundary where we have the identity context for logging later.
    if (rawKey.startsWith('/')) continue;
    out[identity] = rawKey;
  }
  return out;
}
