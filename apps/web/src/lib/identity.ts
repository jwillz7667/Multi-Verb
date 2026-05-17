/**
 * Identity helpers — bridge the Auth.js cuid user id to the domain
 * schema's uuid foreign keys.
 *
 * The Auth.js v5 Postgres adapter uses cuid-shaped strings for
 * `users.id` (a text column). Domain tables (`studies.org_id`,
 * `studies.created_by`, etc.) use Postgres `uuid` columns per the
 * brief (§10.1). We need a deterministic mapping so the same user
 * always resolves to the same org/creator uuid across requests,
 * processes, and restarts — otherwise a researcher who signs out and
 * back in would lose access to their own studies.
 *
 * Approach: SHA-256(`org:<userId>`), take the first 16 bytes, force
 * the RFC 4122 version (5) and variant (10xx) bits, and format as the
 * canonical 8-4-4-4-12 hex string. Equivalent to uuid v5 with a
 * private namespace, but with zero npm dependencies.
 *
 * Single-user MVP only: `orgIdForUser(userId) === userId`'s namespace
 * derivative. When the org concept lands (User.orgId column), repos
 * will call a different helper that reads the stored value.
 */

import 'server-only';

import { createHash } from 'node:crypto';

function uuidFromBytes(bytes: Buffer): string {
  // `Buffer#readUInt8` / `writeUInt8` avoid the `noUncheckedIndexedAccess`
  // hole that bracket-access opens. The caller guarantees ≥9 bytes.
  // Set RFC 4122 version 5 bits: byte 6 high nibble = 0101.
  bytes.writeUInt8((bytes.readUInt8(6) & 0x0f) | 0x50, 6);
  // Set RFC 4122 variant bits: byte 8 high two bits = 10.
  bytes.writeUInt8((bytes.readUInt8(8) & 0x3f) | 0x80, 8);
  const hex = bytes.toString('hex');
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20, 32),
  ].join('-');
}

function deterministicUuid(input: string): string {
  const digest = createHash('sha256').update(input).digest();
  // Copy because `set` bits mutates — and we want a fresh buffer for
  // each call so concurrent reuse is safe.
  return uuidFromBytes(Buffer.from(digest.subarray(0, 16)));
}

/**
 * Map an Auth.js user id (cuid) to a stable uuid for tenancy fields.
 *
 * Same input → same output across processes; safe to use as `org_id`
 * + `created_by` until a real org table lands.
 */
export function orgIdForUser(userId: string): string {
  return deterministicUuid(`org:${userId}`);
}

/** Mirror of `orgIdForUser` for the `created_by` audit column.
 * Distinct namespace so a future migration can re-key one without
 * silently shuffling the other. */
export function userUuidForAudit(userId: string): string {
  return deterministicUuid(`user:${userId}`);
}
