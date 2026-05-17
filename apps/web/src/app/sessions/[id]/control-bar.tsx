'use client';

/**
 * ControlBar — researcher live-control surface (brief §5, P5 L8).
 *
 * The control bar exposes the non-modal half of the researcher command
 * plane: mute/unmute, quietness dial, pause/resume, flag, end. Each
 * widget posts a typed `ResearcherCommand` envelope to
 * `/api/sessions/{id}/commands`, which mints `command_id`, stamps
 * `researcher_id` from the auth session, and XADDs onto the per-session
 * Redis stream the engine drains at the top of every tick.
 *
 * State model:
 *   - Mute / pause are tracked as optimistic booleans. The engine is
 *     the source of truth (its `is_paused` / `moderator_muted` ride the
 *     state-snapshot stream), so a drifted optimistic toggle re-syncs
 *     as soon as the next snapshot arrives. The optimistic update is
 *     just to keep the button label responsive while the POST is in
 *     flight.
 *   - Quietness lives on a 1–10 dial; position 5 ≈ engine defaults
 *     (3 utterances / 10min, 30s floor). Slider changes are debounced
 *     350ms so a drag doesn't flood the command stream.
 *   - Flag opens an inline note field rather than a modal (modals are
 *     reserved for the spoken interventions in L9). End opens an
 *     inline confirm to keep the destructive action a deliberate two-click.
 *
 * The whole bar disables itself when `status !== "live"` — pre-start
 * there's no agent listening, post-end the route will 409 anyway.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { SessionStatus } from '@/features/sessions';

interface Props {
  sessionId: string;
  status: SessionStatus;
  onSessionEnded?: () => void;
}

// 1–10 dial → concrete (max_utterances_per_10min, min_seconds_between).
// Position 5 matches `QuietnessBudget`'s engine-side defaults so the UI
// neutrally reflects "no override applied yet." Curve is monotonic and
// asymmetric: each step toward 1 (quieter) raises the silence floor
// more aggressively than each step toward 10 lowers it, which encodes
// the brief's bias-toward-silence principle into the affordance itself.
const QUIETNESS_PRESETS: readonly {
  maxPerTenMin: number;
  minSeconds: number;
}[] = [
  { maxPerTenMin: 1, minSeconds: 120 },
  { maxPerTenMin: 1, minSeconds: 90 },
  { maxPerTenMin: 2, minSeconds: 60 },
  { maxPerTenMin: 2, minSeconds: 45 },
  { maxPerTenMin: 3, minSeconds: 30 },
  { maxPerTenMin: 4, minSeconds: 25 },
  { maxPerTenMin: 5, minSeconds: 20 },
  { maxPerTenMin: 6, minSeconds: 15 },
  { maxPerTenMin: 8, minSeconds: 12 },
  { maxPerTenMin: 10, minSeconds: 10 },
];

const QUIETNESS_DEFAULT_POSITION = 5;
const QUIETNESS_DEBOUNCE_MS = 350;

export interface QuietnessBudgetPayload {
  max_utterances_per_10min: number;
  min_seconds_between_utterances: number;
}

export function quietnessPositionToBudget(position: number): QuietnessBudgetPayload {
  const clamped = Math.min(Math.max(Math.round(position), 1), QUIETNESS_PRESETS.length);
  // After clamp, `clamped` is in [1, presets.length], so this index
  // is structurally always defined. The fallback exists only to keep
  // tsc's `noUncheckedIndexedAccess` happy without a non-null assertion.
  const preset = QUIETNESS_PRESETS[clamped - 1] ?? { maxPerTenMin: 3, minSeconds: 30 };
  return {
    max_utterances_per_10min: preset.maxPerTenMin,
    min_seconds_between_utterances: preset.minSeconds,
  };
}

// Mirror of the route's accepted command_type literals. Kept narrow on
// purpose: L8 only owns the non-spoken control plane. force_prompt /
// force_redirect / force_summary / whisper land in L9.
type ControlBarCommandType =
  | 'mute_moderator'
  | 'unmute_moderator'
  | 'pause_session'
  | 'resume_session'
  | 'set_quietness_budget'
  | 'flag_moment'
  | 'end_session';

// Payload type is intentionally `unknown` here — the typed validation
// lives in the route's Zod schema (mirrored from the engine's Pydantic
// classes), and a narrower union here would have to be kept in sync
// with both. The component's job is to construct correct envelopes; the
// route is the boundary that rejects malformed ones.
interface CommandBody {
  command_type: ControlBarCommandType;
  payload?: unknown;
}

interface CommandResponseOk {
  ok: true;
  command_id: string;
  issued_at: string;
  stream_entry_id: string;
}

interface CommandResponseError {
  error: string;
}

function isErrorResponse(body: unknown): body is CommandResponseError {
  return (
    typeof body === 'object' && body !== null && 'error' in body && typeof body.error === 'string'
  );
}

// Wraps `JSON.parse` to return `unknown` rather than `any`. This lets
// the call sites narrow with type guards (eg. `isErrorResponse`) and
// keeps the eventual cast to `CommandResponseOk` a lint-visible
// boundary rather than a silent `any → T` widening.
function parseJsonAsUnknown(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

async function postCommand(sessionId: string, body: CommandBody): Promise<CommandResponseOk> {
  const res = await fetch(`/api/sessions/${sessionId}/commands`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  const parsed = parseJsonAsUnknown(text);
  if (!res.ok) {
    const detail = isErrorResponse(parsed) ? parsed.error : `request failed (${res.status})`;
    throw new Error(detail);
  }
  return parsed as CommandResponseOk;
}

export function ControlBar({ sessionId, status, onSessionEnded }: Props): React.ReactElement {
  const [muted, setMuted] = useState(false);
  const [paused, setPaused] = useState(false);
  const [quietness, setQuietness] = useState<number>(QUIETNESS_DEFAULT_POSITION);

  const [muteBusy, setMuteBusy] = useState(false);
  const [pauseBusy, setPauseBusy] = useState(false);
  const [quietnessBusy, setQuietnessBusy] = useState(false);

  const [flagOpen, setFlagOpen] = useState(false);
  const [flagNote, setFlagNote] = useState('');
  const [flagBusy, setFlagBusy] = useState(false);

  const [endConfirmOpen, setEndConfirmOpen] = useState(false);
  const [endReason, setEndReason] = useState('');
  const [endBusy, setEndBusy] = useState(false);

  const [error, setError] = useState<string | null>(null);

  const disabled = status !== 'live';

  // Debounce slider changes — a drag from 5 → 2 fires ~30 React updates
  // in <300ms; we want one POST when the user lets go (or after a
  // typing-pause on keyboard nudge), not 30.
  const quietnessTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSentPosition = useRef<number>(QUIETNESS_DEFAULT_POSITION);
  useEffect(() => {
    return (): void => {
      if (quietnessTimer.current !== null) clearTimeout(quietnessTimer.current);
    };
  }, []);

  const runCommand = useCallback(
    async (body: CommandBody, onSettled: (ok: boolean) => void): Promise<void> => {
      setError(null);
      try {
        await postCommand(sessionId, body);
        onSettled(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'unexpected error');
        onSettled(false);
      }
    },
    [sessionId],
  );

  function onToggleMute(): void {
    if (disabled || muteBusy) return;
    const next = !muted;
    setMuted(next); // optimistic
    setMuteBusy(true);
    void runCommand(
      { command_type: next ? 'mute_moderator' : 'unmute_moderator', payload: {} },
      (ok) => {
        setMuteBusy(false);
        if (!ok) setMuted(!next); // revert on failure
      },
    );
  }

  function onTogglePause(): void {
    if (disabled || pauseBusy) return;
    const next = !paused;
    setPaused(next);
    setPauseBusy(true);
    void runCommand(
      { command_type: next ? 'pause_session' : 'resume_session', payload: {} },
      (ok) => {
        setPauseBusy(false);
        if (!ok) setPaused(!next);
      },
    );
  }

  function onQuietnessChange(value: number): void {
    if (disabled) return;
    setQuietness(value);
    if (quietnessTimer.current !== null) clearTimeout(quietnessTimer.current);
    quietnessTimer.current = setTimeout(() => {
      if (value === lastSentPosition.current) return; // de-dupe identical commits
      lastSentPosition.current = value;
      setQuietnessBusy(true);
      void runCommand(
        {
          command_type: 'set_quietness_budget',
          payload: quietnessPositionToBudget(value),
        },
        () => {
          setQuietnessBusy(false);
        },
      );
    }, QUIETNESS_DEBOUNCE_MS);
  }

  function onSubmitFlag(): void {
    if (disabled || flagBusy) return;
    const trimmed = flagNote.trim();
    setFlagBusy(true);
    void runCommand(
      {
        command_type: 'flag_moment',
        payload: trimmed === '' ? {} : { note: trimmed },
      },
      (ok) => {
        setFlagBusy(false);
        if (ok) {
          setFlagNote('');
          setFlagOpen(false);
        }
      },
    );
  }

  function onConfirmEnd(): void {
    if (disabled || endBusy) return;
    const trimmed = endReason.trim();
    setEndBusy(true);
    void runCommand(
      {
        command_type: 'end_session',
        payload: trimmed === '' ? {} : { reason: trimmed },
      },
      (ok) => {
        setEndBusy(false);
        if (ok) {
          setEndConfirmOpen(false);
          onSessionEnded?.();
        }
      },
    );
  }

  const helperText = (() => {
    if (status === 'scheduled') {
      return 'Controls activate once the moderator joins the room.';
    }
    if (status === 'ended' || status === 'aborted') {
      return 'Session has ended — researcher commands are no longer accepted.';
    }
    return null;
  })();

  return (
    <section
      data-testid="control-bar"
      aria-label="Researcher controls"
      className="border-border-default bg-surface-primary flex flex-col gap-4 rounded-lg border p-6"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-text-primary text-lg font-medium">Controls</h2>
        {helperText !== null && <span className="text-text-tertiary text-xs">{helperText}</span>}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onToggleMute}
          disabled={disabled || muteBusy}
          aria-pressed={muted}
          className={
            muted
              ? 'rounded-md bg-red-600 px-3 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50'
              : 'border-border-default hover:bg-surface-tertiary rounded-md border px-3 py-2 text-sm font-medium transition-colors disabled:opacity-50'
          }
        >
          {muted ? 'Muted' : 'Mute moderator'}
        </button>

        <button
          type="button"
          onClick={onTogglePause}
          disabled={disabled || pauseBusy}
          aria-pressed={paused}
          className="border-border-default hover:bg-surface-tertiary rounded-md border px-3 py-2 text-sm font-medium transition-colors disabled:opacity-50"
        >
          {paused ? 'Resume session' : 'Pause session'}
        </button>

        <button
          type="button"
          onClick={() => {
            if (disabled) return;
            setFlagOpen((v) => !v);
            setFlagNote('');
          }}
          disabled={disabled}
          aria-expanded={flagOpen}
          className="border-border-default hover:bg-surface-tertiary rounded-md border px-3 py-2 text-sm font-medium transition-colors disabled:opacity-50"
        >
          Flag moment
        </button>

        <button
          type="button"
          onClick={() => {
            if (disabled) return;
            setEndConfirmOpen((v) => !v);
            setEndReason('');
          }}
          disabled={disabled}
          aria-expanded={endConfirmOpen}
          className="ml-auto rounded-md border border-red-700 px-3 py-2 text-sm font-medium text-red-700 transition-colors hover:bg-red-50 disabled:opacity-50 dark:hover:bg-red-950/30"
        >
          End session
        </button>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between text-sm">
          <label htmlFor={`${sessionId}-quietness`} className="text-text-primary font-medium">
            Quietness
          </label>
          <span className="text-text-tertiary font-mono text-xs">
            {quietness}/10 · max {quietnessPositionToBudget(quietness).max_utterances_per_10min}/10m
            · ≥{quietnessPositionToBudget(quietness).min_seconds_between_utterances}s
            {quietnessBusy && ' · applying…'}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-text-tertiary text-xs">Quiet</span>
          <input
            id={`${sessionId}-quietness`}
            type="range"
            min={1}
            max={QUIETNESS_PRESETS.length}
            step={1}
            value={quietness}
            disabled={disabled}
            onChange={(e) => {
              onQuietnessChange(Number(e.target.value));
            }}
            aria-label="Quietness budget"
            className="flex-1 accent-current disabled:opacity-50"
          />
          <span className="text-text-tertiary text-xs">Chatty</span>
        </div>
      </div>

      {flagOpen && !disabled && (
        <div className="border-border-default bg-surface-secondary flex flex-col gap-2 rounded-md border p-3">
          <label
            htmlFor={`${sessionId}-flag-note`}
            className="text-text-primary text-sm font-medium"
          >
            Flag note (optional)
          </label>
          <textarea
            id={`${sessionId}-flag-note`}
            value={flagNote}
            onChange={(e) => {
              setFlagNote(e.target.value);
            }}
            rows={2}
            maxLength={1000}
            placeholder="What's worth coming back to?"
            className="border-border-default min-h-[60px] resize-y rounded-md border bg-transparent px-3 py-2 text-sm"
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setFlagOpen(false);
                setFlagNote('');
              }}
              disabled={flagBusy}
              className="text-text-secondary hover:text-text-primary text-sm"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onSubmitFlag}
              disabled={flagBusy}
              className="bg-text-primary text-surface-primary rounded-md px-3 py-1.5 text-sm font-medium hover:opacity-90 disabled:opacity-50"
            >
              {flagBusy ? 'Flagging…' : 'Save flag'}
            </button>
          </div>
        </div>
      )}

      {endConfirmOpen && !disabled && (
        <div className="flex flex-col gap-2 rounded-md border border-red-200 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950/30">
          <p className="text-sm text-red-900 dark:text-red-200">
            Ending the session disconnects the moderator and closes the recording. This cannot be
            undone.
          </p>
          <label
            htmlFor={`${sessionId}-end-reason`}
            className="text-sm font-medium text-red-900 dark:text-red-200"
          >
            Reason (optional)
          </label>
          <input
            id={`${sessionId}-end-reason`}
            type="text"
            value={endReason}
            onChange={(e) => {
              setEndReason(e.target.value);
            }}
            maxLength={500}
            placeholder="e.g. participants left early"
            className="rounded-md border border-red-300 bg-transparent px-3 py-2 text-sm dark:border-red-900"
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setEndConfirmOpen(false);
                setEndReason('');
              }}
              disabled={endBusy}
              className="text-text-secondary hover:text-text-primary text-sm"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onConfirmEnd}
              disabled={endBusy}
              className="rounded-md bg-red-700 px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {endBusy ? 'Ending…' : 'End session'}
            </button>
          </div>
        </div>
      )}

      {error !== null && (
        <p
          role="alert"
          className="rounded-md bg-red-100 px-3 py-2 text-sm text-red-900 dark:bg-red-950/40 dark:text-red-200"
        >
          {error}
        </p>
      )}
    </section>
  );
}
