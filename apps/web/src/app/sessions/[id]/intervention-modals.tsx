'use client';

/**
 * InterventionModals — researcher spoken-intervention surface (P5 L9).
 *
 * The modal-driven half of the command plane (the non-modal half lives
 * in `<ControlBar />`). Four interventions, three of which the mouth
 * layer phrases and one verbatim:
 *
 *   - `force_prompt`   — researcher hint → LLM phrasing, optional target
 *                        participant. Engine writes a decision with
 *                        `source="researcher_manual"`, the mouth still
 *                        runs to phrase `researcher_hint` in voice.
 *   - `force_redirect` — researcher topic → LLM phrasing redirecting the
 *                        conversation. No target.
 *   - `force_summary`  — optional focus → LLM phrasing summarising the
 *                        thread (full thread if focus is empty). No target.
 *   - `whisper`        — verbatim text spoken as-is, bypassing the mouth.
 *                        Engine writes a decision with
 *                        `source="researcher_whisper"`. Optional target.
 *
 * Why a dialog (and not an inline popover like `flag` / `end_session`):
 * spoken interventions require deliberate composition — the researcher
 * is choosing the moderator's words, and a backdrop / focus-trapped
 * dialog signals "this is consequential, finish or cancel before doing
 * anything else." Flag and end are confirm-style affordances, not
 * compose-style affordances, hence the inline popover treatment.
 *
 * Why a lazy SSE subscription: `force_prompt` and `whisper` need the
 * participant roster so the researcher can pick a target. We open the
 * subscription only when one of those modals is open — closed UI does
 * no extra TCP work. The `<ParticipantTiles />` component on the same
 * page already keeps its own subscription open for the participant
 * grid; Phase 6 collapses both into a single shared context provider.
 *
 * Why a single component and not four files: the four modals share
 * boilerplate (backdrop, ESC handling, focus-on-open, busy state,
 * error surface). One file keeps the shared scaffolding shared and
 * each modal body small.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { SessionStatus } from '@/features/sessions';
import {
  parseTranscriptEvent,
  type StateSnapshotTranscriptEvent,
} from '@/features/sessions/events';

interface Props {
  sessionId: string;
  status: SessionStatus;
}

type InterventionKind = 'force_prompt' | 'force_redirect' | 'force_summary' | 'whisper';
type SessionStateShape = StateSnapshotTranscriptEvent['payload']['state'];

interface ParticipantSummary {
  participant_id: string;
  display_name: string;
}

// ---------------------------------------------------------------------------
// HTTP — mirror of control-bar.tsx's `postCommand`. Kept duplicated rather
// than lifted because the bar's surface is narrower than the modal set's,
// and a shared module would have to choose a union that's wider than each
// caller needs (which is what `unknown` would buy us anyway).
// ---------------------------------------------------------------------------

interface CommandBody {
  command_type: InterventionKind;
  // Typed validation lives in the route's Zod schema (`PublishCommandInputSchema`).
  // The component constructs the right shape; the route is the boundary that rejects malformed ones.
  payload: unknown;
}

interface CommandResponseOk {
  ok: true;
  command_id: string;
  issued_at: string;
  stream_entry_id: string;
}

function isErrorBody(body: unknown): body is { error: string } {
  return (
    typeof body === 'object' && body !== null && 'error' in body && typeof body.error === 'string'
  );
}

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
    const detail = isErrorBody(parsed) ? parsed.error : `request failed (${res.status})`;
    throw new Error(detail);
  }
  return parsed as CommandResponseOk;
}

// ---------------------------------------------------------------------------
// Shell — buttons + modal host
// ---------------------------------------------------------------------------

export function InterventionModals({ sessionId, status }: Props): React.ReactElement {
  const [active, setActive] = useState<InterventionKind | null>(null);
  const [participants, setParticipants] = useState<ParticipantSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const disabled = status !== 'live';
  const needsParticipants = active === 'force_prompt' || active === 'whisper';

  // Lazy SSE subscription — open only while a target-bearing modal is up.
  // Snapshot's `state.participants` is keyed by participant_id; we collapse
  // it to a name-sorted array so the <select> renders in a stable order.
  useEffect(() => {
    if (!needsParticipants) return;
    const source = new EventSource(`/api/sessions/${sessionId}/events`);

    const onSnapshot = (raw: MessageEvent<string>): void => {
      const parsed = parseTranscriptEvent(parseJsonAsUnknown(raw.data));
      if (parsed?.type !== 'state_snapshot') return;
      const state: SessionStateShape = parsed.payload.state;
      const next: ParticipantSummary[] = Object.values(state.participants)
        .map((p) => ({ participant_id: p.participant_id, display_name: p.display_name }))
        .sort((a, b) => a.display_name.localeCompare(b.display_name));
      setParticipants(next);
    };

    source.addEventListener('state_snapshot', onSnapshot);
    return (): void => {
      source.removeEventListener('state_snapshot', onSnapshot);
      source.close();
    };
  }, [needsParticipants, sessionId]);

  // ESC closes the active modal — standard dialog affordance.
  useEffect(() => {
    if (active === null) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setActive(null);
    };
    window.addEventListener('keydown', onKey);
    return (): void => {
      window.removeEventListener('keydown', onKey);
    };
  }, [active]);

  const runCommand = useCallback(
    async (kind: InterventionKind, payload: unknown): Promise<boolean> => {
      setError(null);
      try {
        await postCommand(sessionId, { command_type: kind, payload });
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : 'unexpected error');
        return false;
      }
    },
    [sessionId],
  );

  const close = useCallback((): void => {
    setActive(null);
  }, []);

  const helperText = (() => {
    if (status === 'scheduled') {
      return 'Interventions activate once the moderator joins the room.';
    }
    if (status === 'ended' || status === 'aborted') {
      return 'Session has ended — interventions are no longer accepted.';
    }
    return null;
  })();

  return (
    <section
      data-testid="intervention-modals"
      aria-label="Moderator interventions"
      className="border-border-default bg-surface-primary flex flex-col gap-4 rounded-lg border p-6"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-text-primary text-lg font-medium">Interventions</h2>
        {helperText !== null && <span className="text-text-tertiary text-xs">{helperText}</span>}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => {
            setActive('force_prompt');
          }}
          disabled={disabled}
          className="border-border-default hover:bg-surface-tertiary rounded-md border px-3 py-2 text-sm font-medium transition-colors disabled:opacity-50"
        >
          Prompt participant
        </button>
        <button
          type="button"
          onClick={() => {
            setActive('force_redirect');
          }}
          disabled={disabled}
          className="border-border-default hover:bg-surface-tertiary rounded-md border px-3 py-2 text-sm font-medium transition-colors disabled:opacity-50"
        >
          Redirect topic
        </button>
        <button
          type="button"
          onClick={() => {
            setActive('force_summary');
          }}
          disabled={disabled}
          className="border-border-default hover:bg-surface-tertiary rounded-md border px-3 py-2 text-sm font-medium transition-colors disabled:opacity-50"
        >
          Summarise thread
        </button>
        <button
          type="button"
          onClick={() => {
            setActive('whisper');
          }}
          disabled={disabled}
          className="border-border-default hover:bg-surface-tertiary rounded-md border px-3 py-2 text-sm font-medium transition-colors disabled:opacity-50"
        >
          Whisper verbatim
        </button>
      </div>

      {error !== null && (
        <p
          role="alert"
          className="rounded-md bg-red-100 px-3 py-2 text-sm text-red-900 dark:bg-red-950/40 dark:text-red-200"
        >
          {error}
        </p>
      )}

      {active === 'force_prompt' && (
        <ForcePromptModal
          participants={participants}
          onClose={close}
          onSubmit={async (payload) => {
            const ok = await runCommand('force_prompt', payload);
            if (ok) close();
            return ok;
          }}
        />
      )}
      {active === 'force_redirect' && (
        <ForceRedirectModal
          onClose={close}
          onSubmit={async (payload) => {
            const ok = await runCommand('force_redirect', payload);
            if (ok) close();
            return ok;
          }}
        />
      )}
      {active === 'force_summary' && (
        <ForceSummaryModal
          onClose={close}
          onSubmit={async (payload) => {
            const ok = await runCommand('force_summary', payload);
            if (ok) close();
            return ok;
          }}
        />
      )}
      {active === 'whisper' && (
        <WhisperModal
          participants={participants}
          onClose={close}
          onSubmit={async (payload) => {
            const ok = await runCommand('whisper', payload);
            if (ok) close();
            return ok;
          }}
        />
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Shared modal scaffolding
// ---------------------------------------------------------------------------

interface ModalShellProps {
  title: string;
  subtitle?: string;
  onClose: () => void;
  busy: boolean;
  onSubmit: () => void;
  submitLabel: string;
  submitBusyLabel: string;
  submitDisabled: boolean;
  children: React.ReactNode;
}

function ModalShell({
  title,
  subtitle,
  onClose,
  busy,
  onSubmit,
  submitLabel,
  submitBusyLabel,
  submitDisabled,
  children,
}: ModalShellProps): React.ReactElement {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop is a button so click + Enter/Space close naturally; aria-hidden
          keeps it out of the screen-reader narration since ESC is the canonical
          close affordance and the cancel button inside the panel is the focusable one. */}
      <button
        type="button"
        onClick={() => {
          if (!busy) onClose();
        }}
        aria-hidden="true"
        tabIndex={-1}
        className="absolute inset-0 cursor-default bg-black/40"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="border-border-default bg-surface-primary relative z-10 flex w-full max-w-lg flex-col gap-4 rounded-lg border p-6 shadow-lg"
      >
        <div className="flex flex-col gap-1">
          <h3 className="text-text-primary text-lg font-medium">{title}</h3>
          {subtitle !== undefined && <p className="text-text-tertiary text-xs">{subtitle}</p>}
        </div>
        <div className="flex flex-col gap-3">{children}</div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="text-text-secondary hover:text-text-primary text-sm disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onSubmit}
            disabled={busy || submitDisabled}
            className="bg-text-primary text-surface-primary rounded-md px-3 py-1.5 text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >
            {busy ? submitBusyLabel : submitLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

interface ParticipantSelectProps {
  id: string;
  participants: ParticipantSummary[];
  value: string;
  onChange: (next: string) => void;
  label: string;
  emptyOptionLabel: string;
  disabled?: boolean;
}

function ParticipantSelect({
  id,
  participants,
  value,
  onChange,
  label,
  emptyOptionLabel,
  disabled,
}: ParticipantSelectProps): React.ReactElement {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-text-primary text-sm font-medium">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
        }}
        disabled={disabled}
        className="border-border-default rounded-md border bg-transparent px-3 py-2 text-sm disabled:opacity-50"
      >
        <option value="">{emptyOptionLabel}</option>
        {participants.map((p) => (
          <option key={p.participant_id} value={p.participant_id}>
            {p.display_name}
          </option>
        ))}
      </select>
    </div>
  );
}

// Common autofocus helper for a modal's primary textarea. Runs once on
// mount so re-renders triggered by typing don't yank focus mid-keystroke.
function useAutofocus<T extends HTMLElement>(): React.RefObject<T | null> {
  const ref = useRef<T | null>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return ref;
}

// ---------------------------------------------------------------------------
// Force prompt — researcher hint + optional target
// ---------------------------------------------------------------------------

interface ForcePromptPayload {
  prompt: string;
  target_participant_id?: string | null;
}

interface ForcePromptModalProps {
  participants: ParticipantSummary[];
  onClose: () => void;
  onSubmit: (payload: ForcePromptPayload) => Promise<boolean>;
}

function ForcePromptModal({
  participants,
  onClose,
  onSubmit,
}: ForcePromptModalProps): React.ReactElement {
  const [prompt, setPrompt] = useState('');
  const [target, setTarget] = useState('');
  const [busy, setBusy] = useState(false);
  const textareaRef = useAutofocus<HTMLTextAreaElement>();

  const trimmed = prompt.trim();
  const canSubmit = trimmed.length > 0;

  async function handleSubmit(): Promise<void> {
    if (!canSubmit) return;
    setBusy(true);
    const payload: ForcePromptPayload = {
      prompt: trimmed,
      target_participant_id: target === '' ? null : target,
    };
    const ok = await onSubmit(payload);
    if (!ok) setBusy(false);
  }

  return (
    <ModalShell
      title="Prompt a participant"
      subtitle="Your hint is phrased in the moderator's voice. Leave the target blank to address the whole room."
      onClose={onClose}
      busy={busy}
      onSubmit={() => {
        void handleSubmit();
      }}
      submitLabel="Send to moderator"
      submitBusyLabel="Sending…"
      submitDisabled={!canSubmit}
    >
      <ParticipantSelect
        id="force-prompt-target"
        participants={participants}
        value={target}
        onChange={setTarget}
        label="Target participant (optional)"
        emptyOptionLabel="— no specific target —"
        disabled={busy}
      />
      <div className="flex flex-col gap-1">
        <label htmlFor="force-prompt-hint" className="text-text-primary text-sm font-medium">
          Hint
        </label>
        <textarea
          id="force-prompt-hint"
          ref={textareaRef}
          value={prompt}
          onChange={(e) => {
            setPrompt(e.target.value);
          }}
          disabled={busy}
          rows={4}
          maxLength={2000}
          placeholder="e.g. ask about the voice flattening point Maya made"
          className="border-border-default min-h-[88px] resize-y rounded-md border bg-transparent px-3 py-2 text-sm disabled:opacity-50"
        />
        <span className="text-text-tertiary text-xs">
          The moderator phrases this for you — write the intent, not the words.
        </span>
      </div>
    </ModalShell>
  );
}

// ---------------------------------------------------------------------------
// Force redirect — topic only
// ---------------------------------------------------------------------------

interface ForceRedirectPayload {
  topic: string;
}

interface ForceRedirectModalProps {
  onClose: () => void;
  onSubmit: (payload: ForceRedirectPayload) => Promise<boolean>;
}

function ForceRedirectModal({ onClose, onSubmit }: ForceRedirectModalProps): React.ReactElement {
  const [topic, setTopic] = useState('');
  const [busy, setBusy] = useState(false);
  const textareaRef = useAutofocus<HTMLTextAreaElement>();

  const trimmed = topic.trim();
  const canSubmit = trimmed.length > 0;

  async function handleSubmit(): Promise<void> {
    if (!canSubmit) return;
    setBusy(true);
    const ok = await onSubmit({ topic: trimmed });
    if (!ok) setBusy(false);
  }

  return (
    <ModalShell
      title="Redirect topic"
      subtitle="The moderator steers the discussion back to this topic in their own voice."
      onClose={onClose}
      busy={busy}
      onSubmit={() => {
        void handleSubmit();
      }}
      submitLabel="Send to moderator"
      submitBusyLabel="Sending…"
      submitDisabled={!canSubmit}
    >
      <div className="flex flex-col gap-1">
        <label htmlFor="force-redirect-topic" className="text-text-primary text-sm font-medium">
          Topic
        </label>
        <textarea
          id="force-redirect-topic"
          ref={textareaRef}
          value={topic}
          onChange={(e) => {
            setTopic(e.target.value);
          }}
          disabled={busy}
          rows={4}
          maxLength={2000}
          placeholder="e.g. bring us back to the trust question, away from Stack Overflow"
          className="border-border-default min-h-[88px] resize-y rounded-md border bg-transparent px-3 py-2 text-sm disabled:opacity-50"
        />
      </div>
    </ModalShell>
  );
}

// ---------------------------------------------------------------------------
// Force summary — optional focus
// ---------------------------------------------------------------------------

interface ForceSummaryPayload {
  focus?: string | null;
}

interface ForceSummaryModalProps {
  onClose: () => void;
  onSubmit: (payload: ForceSummaryPayload) => Promise<boolean>;
}

function ForceSummaryModal({ onClose, onSubmit }: ForceSummaryModalProps): React.ReactElement {
  const [focus, setFocus] = useState('');
  const [busy, setBusy] = useState(false);
  const textareaRef = useAutofocus<HTMLTextAreaElement>();

  const trimmed = focus.trim();

  async function handleSubmit(): Promise<void> {
    setBusy(true);
    const payload: ForceSummaryPayload = trimmed === '' ? {} : { focus: trimmed };
    const ok = await onSubmit(payload);
    if (!ok) setBusy(false);
  }

  return (
    <ModalShell
      title="Summarise thread"
      subtitle="Leave focus blank to summarise the full thread. Provide a focus to summarise one strand."
      onClose={onClose}
      busy={busy}
      onSubmit={() => {
        void handleSubmit();
      }}
      submitLabel="Send to moderator"
      submitBusyLabel="Sending…"
      submitDisabled={false}
    >
      <div className="flex flex-col gap-1">
        <label htmlFor="force-summary-focus" className="text-text-primary text-sm font-medium">
          Focus (optional)
        </label>
        <textarea
          id="force-summary-focus"
          ref={textareaRef}
          value={focus}
          onChange={(e) => {
            setFocus(e.target.value);
          }}
          disabled={busy}
          rows={3}
          maxLength={2000}
          placeholder="e.g. what we've heard about trust so far"
          className="border-border-default min-h-[72px] resize-y rounded-md border bg-transparent px-3 py-2 text-sm disabled:opacity-50"
        />
      </div>
    </ModalShell>
  );
}

// ---------------------------------------------------------------------------
// Whisper — verbatim text, bypasses LLM
// ---------------------------------------------------------------------------

interface WhisperPayload {
  text: string;
  target_participant_id?: string | null;
}

interface WhisperModalProps {
  participants: ParticipantSummary[];
  onClose: () => void;
  onSubmit: (payload: WhisperPayload) => Promise<boolean>;
}

function WhisperModal({ participants, onClose, onSubmit }: WhisperModalProps): React.ReactElement {
  const [text, setText] = useState('');
  const [target, setTarget] = useState('');
  const [busy, setBusy] = useState(false);
  const textareaRef = useAutofocus<HTMLTextAreaElement>();

  const trimmed = text.trim();
  const canSubmit = trimmed.length > 0;

  async function handleSubmit(): Promise<void> {
    if (!canSubmit) return;
    setBusy(true);
    const payload: WhisperPayload = {
      text: trimmed,
      target_participant_id: target === '' ? null : target,
    };
    const ok = await onSubmit(payload);
    if (!ok) setBusy(false);
  }

  // Visual cue: amber accent so the researcher registers that this is the
  // bypass-the-LLM path — what they type is what gets spoken.
  return (
    <ModalShell
      title="Whisper verbatim"
      subtitle="No phrasing pass — your text is spoken by the moderator exactly as written."
      onClose={onClose}
      busy={busy}
      onSubmit={() => {
        void handleSubmit();
      }}
      submitLabel="Speak verbatim"
      submitBusyLabel="Sending…"
      submitDisabled={!canSubmit}
    >
      <ParticipantSelect
        id="whisper-target"
        participants={participants}
        value={target}
        onChange={setTarget}
        label="Target participant (optional)"
        emptyOptionLabel="— address the whole room —"
        disabled={busy}
      />
      <div className="flex flex-col gap-1">
        <label htmlFor="whisper-text" className="text-text-primary text-sm font-medium">
          Spoken text
        </label>
        <textarea
          id="whisper-text"
          ref={textareaRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
          }}
          disabled={busy}
          rows={4}
          maxLength={2000}
          placeholder="The moderator says this verbatim."
          className="border-border-default min-h-[88px] resize-y rounded-md border bg-amber-50/40 px-3 py-2 text-sm disabled:opacity-50 dark:bg-amber-950/20"
        />
      </div>
    </ModalShell>
  );
}
