/**
 * /studies — list of studies for the current org.
 *
 * Studies hold the reusable configuration (prompt + persona + rules)
 * a session is built from. Researchers create one per piece of
 * research and reuse it across many sessions; the engine snapshots
 * the live row at session start so later edits don't rewrite history.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { listStudies } from '@/features/studies';
import { auth } from '@/lib/auth';
import { orgIdForUser } from '@/lib/identity';

export const dynamic = 'force-dynamic';

export default async function StudiesPage(): Promise<React.ReactElement> {
  const userSession = await auth();
  if (!userSession?.user?.id) {
    redirect('/sign-in?callbackUrl=/studies');
  }
  const studies = await listStudies(orgIdForUser(userSession.user.id));

  return (
    <main className="min-h-screen bg-surface-secondary text-text-primary">
      <div className="mx-auto flex max-w-4xl flex-col gap-6 px-6 py-12">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-medium tracking-tight">Studies</h1>
            <p className="text-text-secondary mt-1 text-sm">
              Reusable session configurations — the moderator persona, prompt and rule pack.
            </p>
          </div>
          <Link
            href="/studies/new"
            className="bg-text-primary text-surface-primary rounded-md px-4 py-2 text-sm font-medium hover:opacity-90"
          >
            New study
          </Link>
        </header>

        {studies.length === 0 ? (
          <div className="border-border-default bg-surface-primary rounded-lg border p-8 text-center">
            <p className="text-text-secondary text-sm">
              No studies yet. Create one to pin a persona, prompt, and rule pack the moderator can
              run.
            </p>
          </div>
        ) : (
          <ul className="border-border-default bg-surface-primary divide-border-default divide-y rounded-lg border">
            {studies.map((s) => (
              <li key={s.id}>
                <Link
                  href={`/studies/${s.id}`}
                  className="hover:bg-surface-tertiary flex items-center justify-between px-4 py-3 transition-colors"
                >
                  <div className="flex flex-col">
                    <span className="text-text-primary text-sm font-medium">{s.name}</span>
                    <span className="text-text-tertiary text-xs">
                      voice: {s.moderatorPersona.voice_provider} / {s.moderatorPersona.voice_id}{' '}
                      &middot; rules {s.rulesVersion}
                    </span>
                  </div>
                  <span className="text-text-tertiary text-xs">{s.createdAt.toLocaleString()}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </main>
  );
}
