/**
 * Unit tests for `parseParticipantRecordingUrls`.
 *
 * The function is the single chokepoint between the Postgres JSONB
 * column and the audio route, so every shape that's plausible-but-wrong
 * gets a pin:
 *   - happy path with mixed identities,
 *   - null + undefined are normalized to empty map,
 *   - array at the top level → empty (caller asked for a record),
 *   - non-string keys / values → dropped silently per the spec,
 *   - absolute-path R2 keys (`/foo`) → dropped (signGetUrl will reject
 *     them, but the boundary here is where we have the identity context
 *     for future logging).
 */

import { describe, expect, it } from 'vitest';

import { parseParticipantRecordingUrls } from './replay-urls';

describe('parseParticipantRecordingUrls', () => {
  it('returns empty for null', () => {
    expect(parseParticipantRecordingUrls(null)).toEqual({});
  });

  it('returns empty for undefined', () => {
    expect(parseParticipantRecordingUrls(undefined)).toEqual({});
  });

  it('returns empty for a top-level array (not a record)', () => {
    expect(parseParticipantRecordingUrls(['a', 'b'])).toEqual({});
  });

  it('returns empty for a top-level primitive', () => {
    expect(parseParticipantRecordingUrls('not-an-object')).toEqual({});
    expect(parseParticipantRecordingUrls(42)).toEqual({});
  });

  it('keeps well-formed identity → key entries', () => {
    const input = {
      'alice-001': 'sessions/abc/participants/alice.mp4',
      'bob-002': 'sessions/abc/participants/bob.mp4',
    };

    expect(parseParticipantRecordingUrls(input)).toEqual({
      'alice-001': 'sessions/abc/participants/alice.mp4',
      'bob-002': 'sessions/abc/participants/bob.mp4',
    });
  });

  it('drops empty-string identities and empty-string keys', () => {
    const input = {
      '': 'sessions/abc/empty-id.mp4',
      good: 'sessions/abc/good.mp4',
      'empty-key': '',
    };

    expect(parseParticipantRecordingUrls(input)).toEqual({
      good: 'sessions/abc/good.mp4',
    });
  });

  it('drops non-string keys (numeric, object, array)', () => {
    const input = {
      good: 'sessions/abc/good.mp4',
      numeric: 42,
      nested: { sub: 'value' },
      array: ['x'],
    };

    expect(parseParticipantRecordingUrls(input)).toEqual({
      good: 'sessions/abc/good.mp4',
    });
  });

  it('drops keys that start with `/` (would break signGetUrl)', () => {
    const input = {
      good: 'sessions/abc/good.mp4',
      absolute: '/sessions/abc/bad.mp4',
    };

    expect(parseParticipantRecordingUrls(input)).toEqual({
      good: 'sessions/abc/good.mp4',
    });
  });

  it('treats a corrupt entry as drop-this-one, not crash-all', () => {
    // Mixed: some good, some bad. A single bad row must not block the
    // good ones from reaching the UI.
    const input = {
      ok: 'sessions/abc/ok.mp4',
      bad_value: null,
      also_ok: 'sessions/abc/also.mp4',
    };

    expect(parseParticipantRecordingUrls(input)).toEqual({
      ok: 'sessions/abc/ok.mp4',
      also_ok: 'sessions/abc/also.mp4',
    });
  });
});
