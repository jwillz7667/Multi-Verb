/**
 * /sessions/new — create a fresh moderated session.
 *
 * Server component shell + a tiny client form. Submits via the
 * existing `POST /api/sessions` route so the same wire contract
 * covers both browser and (later) the CLI / E2E harness.
 */

import { redirect } from 'next/navigation';

import { auth } from '@/lib/auth';

import { NewSessionForm } from './new-session-form';

export const dynamic = 'force-dynamic';

export default async function NewSessionPage(): Promise<React.ReactElement> {
  const userSession = await auth();
  if (!userSession?.user) {
    redirect('/sign-in?callbackUrl=/sessions/new');
  }

  return (
    <main className="min-h-screen bg-surface-secondary text-text-primary">
      <div className="mx-auto flex max-w-xl flex-col gap-6 px-6 py-12">
        <header>
          <h1 className="text-2xl font-medium tracking-tight">New session</h1>
          <p className="text-text-secondary mt-1 text-sm">
            Allocates a LiveKit room and the canonical{' '}
            <code className="font-mono text-xs">sessions</code> row. The moderator agent is
            dispatched the first time you press <em>Start session</em> on the detail page.
          </p>
        </header>
        <NewSessionForm />
      </div>
    </main>
  );
}
