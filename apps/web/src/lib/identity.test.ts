import { describe, expect, it } from 'vitest';

import { orgIdForUser, userUuidForAudit } from './identity';

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;

describe('orgIdForUser', () => {
  it('produces a v5-style canonical uuid', () => {
    expect(orgIdForUser('c0xxx0000abc')).toMatch(UUID_PATTERN);
  });

  it('is deterministic — same input always yields the same uuid', () => {
    const a = orgIdForUser('user-alpha');
    const b = orgIdForUser('user-alpha');
    expect(a).toBe(b);
  });

  it('distinguishes between different user ids', () => {
    expect(orgIdForUser('alpha')).not.toBe(orgIdForUser('beta'));
  });
});

describe('userUuidForAudit', () => {
  it('produces a different uuid than orgIdForUser for the same input', () => {
    // The two helpers must use distinct namespaces — otherwise a future
    // migration that re-keys orgs would silently shift audit rows.
    expect(userUuidForAudit('user-1')).not.toBe(orgIdForUser('user-1'));
  });

  it('is deterministic', () => {
    expect(userUuidForAudit('user-1')).toBe(userUuidForAudit('user-1'));
  });
});
