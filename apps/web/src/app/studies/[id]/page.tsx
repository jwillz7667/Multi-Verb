/**
 * /studies/[id] — edit an existing study.
 *
 * Server component fetches the row scoped to the user's derived
 * orgId; a cross-tenant id 404s here, never 403, so existence isn't
 * leaked. The client form below patches the row via
 * `PATCH /api/studies/[id]`.
 */

import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';

import { getStudy } from '@/features/studies';
import { auth } from '@/lib/auth';
import { orgIdForUser } from '@/lib/identity';

import { EditStudyForm } from './edit-study-form';

export const dynamic = 'force-dynamic';

interface Props {
  params: Promise<{ id: string }>;
}

export default async function EditStudyPage({ params }: Props): Promise<React.ReactElement> {
  const userSession = await auth();
  if (!userSession?.user?.id) {
    redirect('/sign-in?callbackUrl=/studies');
  }
  const { id } = await params;
  const study = await getStudy(id, orgIdForUser(userSession.user.id));
  if (study === null) {
    notFound();
  }

  return (
    <main className="min-h-screen bg-surface-secondary text-text-primary">
      <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-12">
        <header className="flex flex-col gap-1">
          <Link href="/studies" className="text-text-tertiary hover:text-text-primary text-xs">
            ← Back to studies
          </Link>
          <h1 className="text-2xl font-medium tracking-tight">{study.name}</h1>
          <p className="text-text-secondary text-sm">
            Rules version {study.rulesVersion} &middot; created {study.createdAt.toLocaleString()}
          </p>
        </header>
        <EditStudyForm study={study} />
      </div>
    </main>
  );
}
