'use client';

import { useRouter } from 'next/navigation';
import { useState, type SyntheticEvent } from 'react';

import type { ModeratorPersonaInput, StudyRow } from '@/features/studies/client';

import { PersonaFormFields } from '../persona-form-fields';

interface Props {
  study: StudyRow;
}

export function EditStudyForm({ study }: Props): React.ReactElement {
  const router = useRouter();
  const [name, setName] = useState(study.name);
  const [prompt, setPrompt] = useState(study.prompt);
  const [persona, setPersona] = useState<ModeratorPersonaInput>(study.moderatorPersona);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  async function onSubmit(event: SyntheticEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setSavedAt(null);
    try {
      const res = await fetch(`/api/studies/${study.id}`, {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          name,
          prompt,
          moderatorPersona: persona,
        }),
      });
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(detail.error ?? `request failed (${res.status})`);
      }
      setSavedAt(new Date().toLocaleTimeString());
      // Refresh server data so the back-button list reflects the new
      // name/persona without a hard reload.
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'unexpected error');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={(e) => {
        void onSubmit(e);
      }}
      className="flex flex-col gap-6"
    >
      <div className="border-border-default bg-surface-primary flex flex-col gap-4 rounded-lg border p-6">
        <label className="flex flex-col gap-2 text-sm">
          <span className="text-text-primary font-medium">Name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
            }}
            required
            maxLength={200}
            className="border-border-default rounded-md border bg-transparent px-3 py-2 text-sm"
          />
        </label>

        <label className="flex flex-col gap-2 text-sm">
          <span className="text-text-primary font-medium">Research prompt</span>
          <textarea
            value={prompt}
            onChange={(e) => {
              setPrompt(e.target.value);
            }}
            required
            maxLength={4000}
            rows={4}
            className="border-border-default rounded-md border bg-transparent px-3 py-2 text-sm leading-relaxed"
          />
          <span className="text-text-tertiary text-xs">
            Editing the prompt affects the next session that uses this study, not historical ones
            (those snapshotted the previous prompt).
          </span>
        </label>
      </div>

      <PersonaFormFields value={persona} onChange={setPersona} disabled={submitting} />

      {error !== null && (
        <p className="rounded-md bg-red-100 px-3 py-2 text-sm text-red-900">{error}</p>
      )}
      {savedAt !== null && error === null && (
        <p className="rounded-md bg-green-100 px-3 py-2 text-sm text-green-900">
          Saved at {savedAt}
        </p>
      )}

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={submitting}
          className="bg-text-primary text-surface-primary rounded-md px-4 py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50"
        >
          {submitting ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </form>
  );
}
