/**
 * L6 — transcript-flow E2E.
 *
 * Exercises the full Phase 1 pipeline end-to-end:
 *
 *     fake participants ─▶ LiveKit room ─▶ engine (Deepgram STT)
 *           ─▶ Postgres (utterances) ─▶ Redis pub/sub
 *           ─▶ Next.js SSE route ─▶ LiveTranscript (browser)
 *
 * What the test does:
 *   1. Creates a session via the dashboard form.
 *   2. Reads the LiveKit room name from Postgres (the UI doesn't expose
 *      it in a stable selector — the h1 is purely visual chrome).
 *   3. Clicks "Start session" to dispatch the moderator agent.
 *   4. Spawns two `verbio-fake-participant` subprocesses publishing the
 *      checked-in WAV fixtures.
 *   5. Waits for utterance rows from at least two distinct speakers to
 *      arrive in the browser via SSE.
 *   6. Verifies the canonical rows exist in Postgres.
 *
 * Why it skips by default:
 *   The engine worker, LiveKit Server, Postgres, Redis, and a real
 *   Deepgram key all need to be reachable. The skip gate keeps `pnpm
 *   test:e2e` runnable in places where that infra isn't wired up,
 *   without silently turning into a no-op — the skip message lists
 *   exactly what's missing.
 */

import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

import { expect, test } from '@playwright/test';

import { db } from './db';

const FIXTURES_DIR = resolve(import.meta.dirname, 'fixtures');
const ENGINE_DIR = resolve(import.meta.dirname, '../../../../services/engine');

const PARTICIPANT_DURATION_SECONDS = 25;
const UTTERANCE_WAIT_MS = 60_000;

interface FakeParticipantSpec {
  identity: string;
  displayName: string;
  wav: string;
}

const PARTICIPANTS: readonly FakeParticipantSpec[] = [
  { identity: 'e2e-p-1', displayName: 'Maya', wav: 'participant-a.wav' },
  { identity: 'e2e-p-2', displayName: 'Daniel', wav: 'participant-b.wav' },
];

function missingEnvVars(): string[] {
  const required = ['LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET'];
  return required.filter((key) => {
    const value = process.env[key];
    return value === undefined || value.length === 0;
  });
}

test.describe('L6 transcript flow', () => {
  const missing = missingEnvVars();
  test.skip(
    missing.length > 0,
    `set ${missing.join(', ')} + run engine worker (\`uv run verbio-engine-agent\`) ` +
      'and LiveKit Server (`docker compose --profile e2e up livekit`) to enable.',
  );

  // Quick fail-fast for fixtures — without them, the participant
  // subprocess would crash with a less-helpful error.
  test.beforeAll(() => {
    for (const p of PARTICIPANTS) {
      const path = resolve(FIXTURES_DIR, p.wav);
      if (!existsSync(path)) {
        throw new Error(`audio fixture missing: ${path}`);
      }
    }
  });

  test('two fake participants → utterances reach the dashboard and the DB', async ({ page }) => {
    test.setTimeout(180_000);

    // 1) Create a fresh session via the UI.
    await page.goto('/sessions/new');
    await page.getByRole('button', { name: /create session/i }).click();
    await page.waitForURL(/\/sessions\/[0-9a-f-]{36}$/, { timeout: 30_000 });

    const sessionId = page.url().split('/').pop();
    if (sessionId === undefined || sessionId.length === 0) {
      throw new Error(`failed to extract session id from URL: ${page.url()}`);
    }

    const sessionRow = await db.moderatedSession.findUniqueOrThrow({
      where: { id: sessionId },
      select: { id: true, livekitRoomName: true, status: true },
    });

    // 2) Dispatch the moderator agent. The button label switches to
    //    "Re-dispatch agent" once the engine reports `status="live"`,
    //    so we don't reuse this selector after click.
    await page.getByRole('button', { name: /^start session$/i }).click();

    // 3) Wait for the SSE stream to attach. The transcript pill renders
    //    "Live" once the EventSource's `ready` arrives — that's our
    //    signal that the Next.js route subscribed to the channel.
    const transcriptSection = page.locator('section', { hasText: 'Live transcript' });
    await expect(transcriptSection).toBeVisible();
    await expect(transcriptSection.getByText(/^Live$/)).toBeVisible({ timeout: 30_000 });

    // 4) Spawn the two fake participants in parallel. Track them so
    //    we can clean up no matter how the test exits.
    const procs: ChildProcess[] = [];
    try {
      for (const p of PARTICIPANTS) {
        procs.push(
          spawnFakeParticipant({
            roomName: sessionRow.livekitRoomName,
            identity: p.identity,
            displayName: p.displayName,
            wavPath: resolve(FIXTURES_DIR, p.wav),
            durationSeconds: PARTICIPANT_DURATION_SECONDS,
          }),
        );
      }

      // 5) Wait for at least one utterance to render. With two speakers
      //    talking for ~25s, Deepgram should surface finals within a few
      //    seconds of audio arriving at the engine.
      const utteranceItems = transcriptSection.locator('ul > li');
      await expect(utteranceItems.first()).toBeVisible({ timeout: UTTERANCE_WAIT_MS });

      // Hold the assertion until two distinct speaker names appear in
      // the rendered list — proves both participants reached the
      // transcript surface, not just one.
      await expect
        .poll(
          async () => {
            const names = await transcriptSection
              .locator('ul > li')
              .evaluateAll((nodes) =>
                Array.from(
                  new Set(
                    nodes
                      .map((node) => (node.querySelector('span')?.textContent ?? '').trim())
                      .filter((name) => name.length > 0),
                  ),
                ),
              );
            return names.length;
          },
          {
            timeout: UTTERANCE_WAIT_MS,
            message: 'expected ≥2 distinct speaker names in transcript',
          },
        )
        .toBeGreaterThanOrEqual(2);

      // 6) Drain the participant subprocesses. Their exit signals the
      //    audio loop finished; the engine will keep transcribing
      //    in-flight buffered audio briefly afterwards.
      await Promise.all(procs.map(waitForChildExit));
    } finally {
      for (const child of procs) {
        if (child.exitCode === null && child.signalCode === null) {
          child.kill('SIGTERM');
        }
      }
    }

    // 7) Final canonical assertion against Postgres: utterance rows
    //    exist for this session, with at least two distinct
    //    participants represented.
    const utterances = await db.utterance.findMany({
      where: { sessionId },
      include: { participant: { select: { livekitIdentity: true, displayName: true } } },
      orderBy: { startTs: 'asc' },
    });

    expect(
      utterances.length,
      `expected ≥1 utterance row in Postgres for session ${sessionId}`,
    ).toBeGreaterThan(0);

    const finals = utterances.filter((u) => u.isFinal);
    expect(
      finals.length,
      'expected at least one is_final=true utterance (the engine must surface finals, not only interims)',
    ).toBeGreaterThan(0);

    const distinctIdentities = new Set(utterances.map((u) => u.participant.livekitIdentity));
    expect(
      distinctIdentities.size,
      `expected ≥2 distinct participant identities, got ${[...distinctIdentities].join(', ')}`,
    ).toBeGreaterThanOrEqual(2);
  });
});

interface SpawnFakeParticipantInput {
  roomName: string;
  identity: string;
  displayName: string;
  wavPath: string;
  durationSeconds: number;
}

function spawnFakeParticipant(input: SpawnFakeParticipantInput): ChildProcess {
  const livekitUrl = process.env['LIVEKIT_URL'];
  const apiKey = process.env['LIVEKIT_API_KEY'];
  const apiSecret = process.env['LIVEKIT_API_SECRET'];
  if (
    livekitUrl === undefined ||
    apiKey === undefined ||
    apiSecret === undefined ||
    livekitUrl.length === 0 ||
    apiKey.length === 0 ||
    apiSecret.length === 0
  ) {
    throw new Error(
      'spawnFakeParticipant requires LIVEKIT_URL + LIVEKIT_API_KEY + LIVEKIT_API_SECRET',
    );
  }

  // `uv run --project` resolves the engine virtualenv without us having
  // to cd; the working directory stays at the web package so any
  // relative paths in stderr point somewhere intelligible.
  const child = spawn(
    'uv',
    [
      'run',
      '--project',
      ENGINE_DIR,
      'verbio-fake-participant',
      '--livekit-url',
      livekitUrl,
      '--api-key',
      apiKey,
      '--api-secret',
      apiSecret,
      '--room',
      input.roomName,
      '--identity',
      input.identity,
      '--display-name',
      input.displayName,
      '--wav',
      input.wavPath,
      '--duration',
      String(input.durationSeconds),
    ],
    {
      stdio: ['ignore', 'inherit', 'inherit'],
      env: process.env,
    },
  );

  child.on('error', (err) => {
    // `spawn` errors (ENOENT for `uv`, EACCES, …) only fire if the
    // process can't be launched. Surface them — silent failure here
    // would have the test time out at the utterance assertion with no
    // hint as to why.
    console.error(`[fake-participant ${input.identity}] spawn error: ${err.message}`);
  });

  return child;
}

function waitForChildExit(child: ChildProcess): Promise<void> {
  return new Promise<void>((resolveFn, rejectFn) => {
    if (child.exitCode !== null || child.signalCode !== null) {
      resolveFn();
      return;
    }
    child.once('exit', (code, signal) => {
      // Exit code 0 is clean. SIGTERM happens when we abort the
      // process in `finally`; that's expected, not a test failure.
      if (code === 0 || code === null || signal === 'SIGTERM') {
        resolveFn();
      } else {
        rejectFn(
          new Error(`fake-participant exited with code=${String(code)} signal=${String(signal)}`),
        );
      }
    });
  });
}
