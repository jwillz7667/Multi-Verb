/**
 * Tests for the flagged audio clip extraction primitives.
 *
 * Pins:
 *   - `computeClipWindow` is symmetric around the flag, clamps the
 *     start to ≥ 0, and always returns the full padSec*2 duration.
 *   - `buildFfmpegArgs` puts `-ss` BEFORE `-i` (HTTP byte-seek path)
 *     and pipes mp3 to stdout — both load-bearing for the route.
 *   - `spawnFfmpegClipStream` wires stdout into a Web ReadableStream
 *     and resolves `done` with the child's exit code.
 *   - `makeClipFilename` sanitises room names + keeps filename safe
 *     across OSes; uses a compact ISO timestamp.
 */

import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';

import { describe, expect, it, vi } from 'vitest';

import {
  buildFfmpegArgs,
  CLIP_AUDIO_BITRATE,
  computeClipWindow,
  DEFAULT_CLIP_PAD_SEC,
  makeClipFilename,
  spawnFfmpegClipStream,
  type SpawnFn,
} from './audio-clip';

import type { ChildProcessWithoutNullStreams } from 'node:child_process';

function makeFakeChild(opts: {
  stdoutChunks: Uint8Array[];
  stderrChunks?: Uint8Array[];
  exitCode?: number;
}): ChildProcessWithoutNullStreams {
  const stdout = Readable.from(opts.stdoutChunks);
  const stderr = Readable.from(opts.stderrChunks ?? []);
  const emitter = new EventEmitter();
  // Tie the child's `close` event to the stdout's `end` so the consumer
  // sees a realistic lifecycle (data → end → close → exit code).
  stdout.on('end', () => {
    setImmediate(() => {
      emitter.emit('close', opts.exitCode ?? 0);
    });
  });
  return Object.assign(emitter, { stdout, stderr }) as unknown as ChildProcessWithoutNullStreams;
}

describe('computeClipWindow', () => {
  const sessionStart = new Date('2026-05-01T10:00:00.000Z');

  it('centers the window on the flag with the default ±15s pad', () => {
    const window = computeClipWindow(new Date('2026-05-01T10:05:00.000Z'), sessionStart);
    // Flag is 300s in; default pad is 15 → start at 285s, 30s long.
    expect(window.startSec).toBeCloseTo(285);
    expect(window.durationSec).toBe(DEFAULT_CLIP_PAD_SEC * 2);
  });

  it('honors a caller-supplied pad', () => {
    const window = computeClipWindow(
      new Date('2026-05-01T10:01:00.000Z'),
      sessionStart,
      45, // 90s window
    );
    expect(window.startSec).toBeCloseTo(15);
    expect(window.durationSec).toBe(90);
  });

  it('clamps startSec to 0 when the flag lands before the pad', () => {
    const window = computeClipWindow(new Date('2026-05-01T10:00:05.000Z'), sessionStart);
    // Flag is 5s in, pad is 15 → would be -10s. Clamp to 0; duration
    // intact so the researcher still gets the full 30s of audio.
    expect(window.startSec).toBe(0);
    expect(window.durationSec).toBe(DEFAULT_CLIP_PAD_SEC * 2);
  });

  it('handles fractional-second flag offsets via floating-point seconds', () => {
    const window = computeClipWindow(new Date('2026-05-01T10:00:45.750Z'), sessionStart, 10);
    // 45.75s - 10 = 35.75s start; 20s duration.
    expect(window.startSec).toBeCloseTo(35.75, 6);
    expect(window.durationSec).toBe(20);
  });
});

describe('buildFfmpegArgs', () => {
  it('places `-ss` BEFORE `-i` so seek is HTTP byte-fast, not stream-from-zero', () => {
    const args = buildFfmpegArgs('https://example.com/recording.mp4', {
      startSec: 12.5,
      durationSec: 30,
    });
    const ssIdx = args.indexOf('-ss');
    const inputIdx = args.indexOf('-i');
    expect(ssIdx).toBeGreaterThanOrEqual(0);
    expect(inputIdx).toBeGreaterThan(ssIdx);
  });

  it('asks for an mp3 stream on stdout — no temp files', () => {
    const args = buildFfmpegArgs('https://example.com/x.mp4', { startSec: 0, durationSec: 10 });
    expect(args).toContain('libmp3lame');
    expect(args).toContain('-b:a');
    expect(args).toContain(CLIP_AUDIO_BITRATE);
    expect(args[args.length - 1]).toBe('pipe:1');
  });

  it('drops the video track (`-vn`) so the mp3 carries audio only', () => {
    const args = buildFfmpegArgs('https://example.com/x.mp4', { startSec: 0, durationSec: 10 });
    expect(args).toContain('-vn');
  });

  it('encodes start + duration as fixed-precision seconds for deterministic seeking', () => {
    const args = buildFfmpegArgs('https://example.com/x.mp4', {
      startSec: 7.123456,
      durationSec: 30,
    });
    // `.toFixed(3)` — ffmpeg accepts decimal seconds but is happiest
    // with a stable string form; raw JS numbers can render `7.1234560000003`.
    expect(args).toContain('7.123');
    expect(args).toContain('30.000');
  });

  it('embeds the input URL verbatim — no shell-quoting, since spawn argv is execve-safe', () => {
    const url = 'https://r2.cloudflarestorage.com/bucket/sessions/x/recording.mp4?sig=abc%2Fdef';
    const args = buildFfmpegArgs(url, { startSec: 0, durationSec: 5 });
    const inputIdx = args.indexOf('-i');
    expect(args[inputIdx + 1]).toBe(url);
  });
});

describe('spawnFfmpegClipStream', () => {
  it('returns ffmpeg stdout as a Web ReadableStream and resolves done with the exit code', async () => {
    const chunks = [new Uint8Array([0x49, 0x44, 0x33]), new Uint8Array([0x00, 0xff, 0xfb])];
    const spawnFn = vi.fn<SpawnFn>(() => makeFakeChild({ stdoutChunks: chunks, exitCode: 0 }));

    const { body, done } = spawnFfmpegClipStream({
      inputUrl: 'https://example.com/x.mp4',
      window: { startSec: 0, durationSec: 5 },
      spawnFn,
    });

    const reader = body.getReader();
    const received: number[] = [];
    for (;;) {
      const { value, done: streamDone } = await reader.read();
      if (streamDone) break;
      received.push(...value);
    }
    expect(received).toEqual([0x49, 0x44, 0x33, 0x00, 0xff, 0xfb]);
    await expect(done).resolves.toBe(0);
  });

  it('passes the right binary path + argv to spawn', () => {
    const spawnFn = vi.fn<SpawnFn>(() => makeFakeChild({ stdoutChunks: [], exitCode: 0 }));
    spawnFfmpegClipStream({
      inputUrl: 'https://example.com/y.mp4',
      window: { startSec: 1.5, durationSec: 10 },
      ffmpegPath: '/usr/local/bin/ffmpeg',
      spawnFn,
    });
    expect(spawnFn).toHaveBeenCalledOnce();
    const [bin, args, options] = spawnFn.mock.calls[0] ?? [];
    expect(bin).toBe('/usr/local/bin/ffmpeg');
    expect(args).toContain('-i');
    expect(args).toContain('https://example.com/y.mp4');
    expect(options).toEqual({ stdio: ['ignore', 'pipe', 'pipe'] });
  });

  it('surfaces a non-zero exit code on done', async () => {
    const spawnFn = vi.fn<SpawnFn>(() => makeFakeChild({ stdoutChunks: [], exitCode: 134 }));
    const { body, done } = spawnFfmpegClipStream({
      inputUrl: 'https://example.com/z.mp4',
      window: { startSec: 0, durationSec: 1 },
      spawnFn,
    });
    // Drain the empty body so the readable can `end` and trigger close.
    const reader = body.getReader();
    while (!(await reader.read()).done) {
      /* drain */
    }
    await expect(done).resolves.toBe(134);
  });
});

describe('makeClipFilename', () => {
  it('namespaces by room + flag-id prefix + compact ISO timestamp', () => {
    const flagTs = new Date('2026-05-01T10:03:45.250Z');
    expect(makeClipFilename('room-alpha', 'flag-12345678abcdef', flagTs)).toBe(
      'clip-room-alpha-flag-123-20260501T100345Z.mp3',
    );
  });

  it('sanitises room names containing slashes, spaces, or quotes', () => {
    const flagTs = new Date('2026-05-01T10:00:00.000Z');
    const filename = makeClipFilename('study/cohort #4 "ops"', 'a1b2c3d4-9999', flagTs);
    expect(filename).not.toMatch(/["#/\s]/);
    expect(filename.endsWith('.mp3')).toBe(true);
  });

  it('truncates long room names to keep filesystem-safe filenames', () => {
    const flagTs = new Date('2026-05-01T10:00:00.000Z');
    const longRoom = 'x'.repeat(200);
    const filename = makeClipFilename(longRoom, 'abcdef12', flagTs);
    // Bounded by the 80-char room slice + the fixed prefix/suffix.
    expect(filename.length).toBeLessThan(140);
  });
});
