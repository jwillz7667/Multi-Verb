/**
 * /studies/new — create a fresh study.
 *
 * Server component shell + a client form. Posts to the existing
 * `POST /api/studies` route so the wire contract is the same shape
 * tests pin and the CLI can drive.
 */

import { redirect } from 'next/navigation';

import { auth } from '@/lib/auth';

import { NewStudyForm } from './new-study-form';

export const dynamic = 'force-dynamic';

export default async function NewStudyPage(): Promise<React.ReactElement> {
  const userSession = await auth();
  if (!userSession?.user) {
    redirect('/sign-in?callbackUrl=/studies/new');
  }

  return (
    <main className="min-h-screen bg-surface-secondary text-text-primary">
      <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-12">
        <header>
          <h1 className="text-2xl font-medium tracking-tight">New study</h1>
          <p className="text-text-secondary mt-1 text-sm">
            Studies pin a research prompt, a moderator persona, and the active rule pack. Sessions
            snapshot the live study config at start, so later edits don&apos;t change historical
            sessions.
          </p>
        </header>
        <NewStudyForm />
      </div>
    </main>
  );
}
