'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState, type SyntheticEvent } from 'react';

import type { ModeratorPersonaInput, StudyRow } from '@/features/studies';
import { listVoicesForProvider } from '@/features/studies';

import { PersonaFormFields } from '../persona-form-fields';

const DEFAULT_PROVIDER = 'cartesia' as const;

function initialPersona(): ModeratorPersonaInput {
  // Seed the form with the brief's recommended defaults (warm, neutral)
  // and the first Cartesia voice. The researcher overrides anything
  // they want before submit.
  const firstVoice = listVoicesForProvider(DEFAULT_PROVIDER)[0];
  return {
    style_prompt: '',
    tone: 'warm',
    formality: 'neutral',
    voice_provider: DEFAULT_PROVIDER,
    voice_id: firstVoice?.voiceId ?? '',
  };
}

export function NewStudyForm(): React.ReactElement {
  const router = useRouter();
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [persona, setPersona] = useState<ModeratorPersonaInput>(initialPersona);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: SyntheticEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch('/api/studies', {
        method: 'POST',
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
      const created = (await res.json()) as StudyRow;
      router.push(`/studies/${created.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'unexpected error');
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
            placeholder="Q2 banking pilot"
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
            placeholder="What participants are being asked to talk about. The moderator uses this to detect topic drift."
            required
            maxLength={4000}
            rows={4}
            className="border-border-default rounded-md border bg-transparent px-3 py-2 text-sm leading-relaxed"
          />
        </label>
      </div>

      <PersonaFormFields value={persona} onChange={setPersona} disabled={submitting} />

      {error !== null && (
        <p className="rounded-md bg-red-100 px-3 py-2 text-sm text-red-900">{error}</p>
      )}

      <div className="flex justify-end gap-3">
        <Link
          href="/studies"
          className="text-text-secondary hover:text-text-primary px-3 py-2 text-sm"
        >
          Cancel
        </Link>
        <button
          type="submit"
          disabled={submitting}
          className="bg-text-primary text-surface-primary rounded-md px-4 py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50"
        >
          {submitting ? 'Creating…' : 'Create study'}
        </button>
      </div>
    </form>
  );
}
