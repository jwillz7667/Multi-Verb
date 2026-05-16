'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState, type SyntheticEvent } from 'react';

import type { CreatedSession } from '@/features/sessions';

export function NewSessionForm(): React.ReactElement {
  const router = useRouter();
  const [scheduledStart, setScheduledStart] = useState<string>('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: SyntheticEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch('/api/sessions', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          scheduledStart:
            scheduledStart === '' ? undefined : new Date(scheduledStart).toISOString(),
        }),
      });
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(detail.error ?? `request failed (${res.status})`);
      }
      const created = (await res.json()) as CreatedSession;
      router.push(`/sessions/${created.id}`);
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
      className="border-border-default bg-surface-primary flex flex-col gap-4 rounded-lg border p-6"
    >
      <label className="flex flex-col gap-2 text-sm">
        <span className="text-text-primary font-medium">Scheduled start (optional)</span>
        <input
          type="datetime-local"
          value={scheduledStart}
          onChange={(e) => {
            setScheduledStart(e.target.value);
          }}
          className="border-border-default rounded-md border bg-transparent px-3 py-2 text-sm"
        />
        <span className="text-text-tertiary text-xs">
          Used for the dashboard schedule view. Doesn&apos;t prevent earlier joins.
        </span>
      </label>

      {error !== null && (
        <p className="rounded-md bg-red-100 px-3 py-2 text-sm text-red-900">{error}</p>
      )}

      <div className="flex justify-end gap-3">
        <Link
          href="/sessions"
          className="text-text-secondary hover:text-text-primary px-3 py-2 text-sm"
        >
          Cancel
        </Link>
        <button
          type="submit"
          disabled={submitting}
          className="bg-text-primary text-surface-primary rounded-md px-4 py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50"
        >
          {submitting ? 'Creating…' : 'Create session'}
        </button>
      </div>
    </form>
  );
}
