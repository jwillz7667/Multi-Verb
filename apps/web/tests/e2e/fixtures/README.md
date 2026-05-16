# Audio fixtures for the L6 transcript-flow E2E

Each `participant-*.wav` is a 16-bit PCM mono clip at 24 kHz of a
short sentence — short enough to keep the repo lean (< 200 KB) and
deterministic enough that Deepgram Nova transcribes the same words
on every run.

The fake-participant publisher loops each clip for the duration of
the E2E run (`--duration` flag). Two distinct clips/speakers exist
so the dashboard can show two rows distinguished by participant.

## Regenerating

These files are checked in. To rebuild them (e.g., new sample
text), run from the repo root:

```bash
uv run --project services/engine \
  python -m verbio_engine.devtools.generate_fixture_audio \
  --out apps/web/tests/e2e/fixtures
```

The generator uses Cartesia Sonic (set `CARTESIA_API_KEY`) to keep
the voice profile consistent with the production TTS stack; the
output is then resampled + truncated to fit the 24 kHz / mono /
short-clip target. The committed clips don't require the key to run
the E2E — only regeneration does.
