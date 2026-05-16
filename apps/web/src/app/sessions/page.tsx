/**
 * /sessions — researcher dashboard listing recent sessions.
 *
 * Phase 1 surface: minimal listing + a create button. Phase 2+ will
 * scope this to the user's organisation, surface study membership,
 * and add search / filter / pagination.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { listRecentSessions } from '@/features/sessions';
import { auth } from '@/lib/auth';

export const dynamic = 'force-dynamic';

export default async function SessionsPage(): Promise<React.ReactElement> {
  const userSession = await auth();
  if (!userSession?.user) {
    redirect('/sign-in?callbackUrl=/sessions');
  }
  const sessions = await listRecentSessions();

  return (
    <main className="min-h-screen bg-surface-secondary text-text-primary">
      <div className="mx-auto flex max-w-4xl flex-col gap-6 px-6 py-12">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-medium tracking-tight">Sessions</h1>
            <p className="text-text-secondary mt-1 text-sm">
              Recently created moderated sessions, newest first.
            </p>
          </div>
          <Link
            href="/sessions/new"
            className="bg-text-primary text-surface-primary rounded-md px-4 py-2 text-sm font-medium hover:opacity-90"
          >
            New session
          </Link>
        </header>

        {sessions.length === 0 ? (
          <div className="border-border-default bg-surface-primary rounded-lg border p-8 text-center">
            <p className="text-text-secondary text-sm">
              No sessions yet. Create one to dispatch the moderator into a LiveKit room.
            </p>
          </div>
        ) : (
          <ul className="border-border-default bg-surface-primary divide-border-default divide-y rounded-lg border">
            {sessions.map((s) => (
              <li key={s.id}>
                <Link
                  href={`/sessions/${s.id}`}
                  className="hover:bg-surface-tertiary flex items-center justify-between px-4 py-3 transition-colors"
                >
                  <div className="flex flex-col">
                    <span className="text-text-primary font-mono text-sm">{s.livekitRoomName}</span>
                    <span className="text-text-tertiary text-xs">
                      created {s.createdAt.toLocaleString()}
                    </span>
                  </div>
                  <SessionStatusBadge status={s.status} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </main>
  );
}

function SessionStatusBadge({ status }: { status: string }): React.ReactElement {
  const tone = (() => {
    switch (status) {
      case 'live':
        return 'bg-green-100 text-green-900';
      case 'ended':
        return 'bg-gray-100 text-gray-700';
      case 'aborted':
        return 'bg-red-100 text-red-900';
      default:
        return 'bg-blue-100 text-blue-900';
    }
  })();
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}>{status}</span>;
}
