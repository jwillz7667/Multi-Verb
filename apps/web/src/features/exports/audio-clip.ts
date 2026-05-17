/**
 * Flagged audio clip export — extraction primitives.
 *
 * Researchers drop bookmark `session_flags` mid-session ("come back to
 * this moment"). The replay UI exposes a downloadable .mp3 clip
 * centered on each flag's `ts`, sourced from the LiveKit composite
 * recording in R2.
 *
 * The route owns auth + lookups + signing the R2 URL; this module owns
 * the deterministic windowing math and the ffmpeg invocation that
 * actually cuts the clip. Splitting them out lets the route stay a
 * thin assembler and keeps the math + the spawn shape unit-testable in
 * isolation (no R2, no ffmpeg binary).
 *
 * ffmpeg is invoked as a child process — we don't ship a JS-native
 * decoder because the composite is mp4/AAC and a pure-JS pipeline
 * would multiply Vercel function size and CPU time for no gain. The
 * binary is expected at `$FFMPEG_PATH` (falls back to `ffmpeg` on
 * PATH); Railway and local dev have it via `apt-get install ffmpeg` /
 * `brew install ffmpeg`. Vercel deployments rely on the Node runtime
 * having ffmpeg available — set `FFMPEG_PATH` to a bundled binary or
 * an installer-provided path if the default lookup misses.
 *
 * Why `-ss` BEFORE `-i`: ffmpeg's input seek is byte-fast over an HTTP
 * URL because the demuxer uses HTTP Range requests against the
 * pre-signed R2 link rather than streaming-and-discarding from byte 0.
 * For a 60-min mp4 the difference is seconds vs. minutes. Output
 * seek (`-ss` after `-i`) would be accurate-frame but slow; we prefer
 * fast seek and accept the small drift (≤ keyframe interval, ~2s) at
 * the clip start — researchers asked for "the moment", not "the exact
 * sample", and the ±15s pad more than covers the drift.
 */

import 'server-only';

import { type ChildProcessWithoutNullStreams, spawn } from 'node:child_process';
import { Readable } from 'node:stream';

export const DEFAULT_CLIP_PAD_SEC = 15;
export const CLIP_AUDIO_BITRATE = '128k';
const FFMPEG_DEFAULT_BIN = 'ffmpeg';

export interface ClipWindow {
  /** Seconds from session start where the clip begins. Clamped to ≥ 0. */
  startSec: number;
  /** Total clip length in seconds. Always 2 × padSec. */
  durationSec: number;
}

/**
 * Position a clip window around a flag timestamp.
 *
 * The window is symmetric: `[flagTs - padSec, flagTs + padSec]`. If the
 * flag landed before `padSec` into the session (rare — flags are usually
 * mid-conversation), the window is shifted to start at 0 but keeps its
 * full length, so the researcher gets `2 × padSec` of audio either way.
 *
 * `padSec` defaults to 15 (30s window). The window only describes what
 * we ask ffmpeg to cut — the engine's full recording is unaffected, so
 * a future "scrub the clip wider" UI can just re-request with a larger
 * pad.
 */
export function computeClipWindow(
  flagTs: Date,
  sessionStartTs: Date,
  padSec: number = DEFAULT_CLIP_PAD_SEC,
): ClipWindow {
  const offsetSec = (flagTs.getTime() - sessionStartTs.getTime()) / 1000;
  const startSec = Math.max(0, offsetSec - padSec);
  const durationSec = padSec * 2;
  return { startSec, durationSec };
}

/**
 * Build the ffmpeg argv for extracting an mp3 clip from the input URL.
 *
 * Exported separately from the spawn so tests can assert the exact
 * argv without invoking ffmpeg. The flag order matters (`-ss` before
 * `-i` enables byte-fast HTTP seek — see module docstring); changing
 * it changes the latency profile dramatically, so the test pins it.
 */
export function buildFfmpegArgs(inputUrl: string, window: ClipWindow): string[] {
  return [
    '-loglevel',
    'error',
    '-ss',
    window.startSec.toFixed(3),
    '-t',
    window.durationSec.toFixed(3),
    '-i',
    inputUrl,
    // Drop video; we only ship the audio track to keep file size + CPU low.
    '-vn',
    '-acodec',
    'libmp3lame',
    '-b:a',
    CLIP_AUDIO_BITRATE,
    '-f',
    'mp3',
    // Write encoded mp3 to stdout — the route pipes it straight into
    // the Response body without ever touching the filesystem.
    'pipe:1',
  ];
}

export type SpawnFn = (
  command: string,
  args: readonly string[],
  options: { stdio: ['ignore', 'pipe', 'pipe'] },
) => ChildProcessWithoutNullStreams;

export interface ClipExtractOptions {
  inputUrl: string;
  window: ClipWindow;
  /** Defaults to `process.env.FFMPEG_PATH ?? 'ffmpeg'`. */
  ffmpegPath?: string;
  /** Injected for tests so we don't spawn a real process. */
  spawnFn?: SpawnFn;
}

export interface ClipExtractResult {
  /**
   * Web ReadableStream of mp3 bytes — ready to hand to `new Response()`.
   * Backpressure flows through to ffmpeg's stdout buffer; if the client
   * stops reading, ffmpeg will block, then exit on SIGPIPE when the
   * stream is cancelled.
   */
  body: ReadableStream<Uint8Array>;
  /**
   * Resolves with ffmpeg's exit code once the process closes. Callers
   * may `await` to detect non-zero exits for logging, but should NOT
   * delay sending the Response on it — the body is already streaming
   * by the time ffmpeg finishes.
   */
  done: Promise<number>;
  /**
   * Buffer-concatenated stderr (truncated to a sane length) for
   * diagnostics on non-zero exit. Resolves with the same lifecycle as
   * `done`.
   */
  stderr: Promise<string>;
}

const STDERR_TAIL_BYTES = 4_096;

/**
 * Spawn ffmpeg and expose its stdout as a Web ReadableStream + a done
 * promise. Caller is responsible for surfacing non-zero exit codes
 * (typically by logging stderr after the response has been sent).
 *
 * Why we don't `await` first-byte before returning: doing so would let
 * a slow R2 fetch delay the response headers past the typical proxy
 * timeout. Letting the stream open immediately means the browser sees
 * progress; if ffmpeg fails before producing output, the body ends up
 * empty and the browser shows a 0-byte mp3, which is honest about the
 * failure mode and lets the operator see the stderr in logs.
 */
export function spawnFfmpegClipStream(opts: ClipExtractOptions): ClipExtractResult {
  const spawnFn = opts.spawnFn ?? (spawn as SpawnFn);
  const ffmpegBin = opts.ffmpegPath ?? process.env['FFMPEG_PATH'] ?? FFMPEG_DEFAULT_BIN;
  const args = buildFfmpegArgs(opts.inputUrl, opts.window);

  const child = spawnFn(ffmpegBin, args, { stdio: ['ignore', 'pipe', 'pipe'] });

  // `Readable.toWeb` is the supported bridge from a Node Readable into
  // the WHATWG ReadableStream that `Response` accepts. We cast to the
  // typed-array variant because ffmpeg only ever writes binary chunks.
  const body = Readable.toWeb(child.stdout) as ReadableStream<Uint8Array>;

  const done = new Promise<number>((resolve) => {
    child.on('close', (code) => {
      resolve(code ?? 1);
    });
    child.on('error', () => {
      // Spawn failure surfaces as `error`, not `close`. Resolve with 1
      // so the caller's `await done` doesn't hang; the empty body the
      // client receives is the honest failure signal.
      resolve(1);
    });
  });

  const stderr = new Promise<string>((resolve) => {
    const chunks: Buffer[] = [];
    let collected = 0;
    child.stderr.on('data', (chunk: Buffer) => {
      if (collected >= STDERR_TAIL_BYTES) return;
      chunks.push(chunk);
      collected += chunk.length;
    });
    child.stderr.on('end', () => {
      resolve(Buffer.concat(chunks).toString('utf8').slice(0, STDERR_TAIL_BYTES));
    });
    child.stderr.on('error', () => {
      resolve('');
    });
  });

  return { body, done, stderr };
}

/**
 * Build the download filename a researcher sees in their browser.
 *
 * Pattern: `clip-<room>-<flag-prefix>-<flag-ts-compact>.mp3`. The room
 * name is sanitised the same way as the transcript / decision / snapshot
 * exports for cross-format consistency. The flag id prefix (first 8
 * chars of the UUID) keeps the name short while still letting an
 * analyst paste it back into a `WHERE id LIKE ...` query.
 */
export function makeClipFilename(roomName: string, flagId: string, flagTs: Date): string {
  const safeRoom = roomName.replace(/[^A-Za-z0-9._-]/g, '-').slice(0, 80);
  const shortFlag = flagId.slice(0, 8);
  // Compact ISO without separators — keeps filenames legal on every OS
  // and grep-friendly when researchers download a dozen clips into a
  // single folder.
  const compactTs = flagTs
    .toISOString()
    .replace(/[-:]/g, '')
    .replace(/\.\d{3}Z$/, 'Z');
  return `clip-${safeRoom}-${shortFlag}-${compactTs}.mp3`;
}
