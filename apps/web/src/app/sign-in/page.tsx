/**
 * /sign-in — Resend magic-link entry point.
 *
 * Server component renders the page chrome; the actual submission is
 * a Server Action that calls `signIn("resend", ...)` from auth.ts.
 * If AUTH_RESEND_KEY is not configured we render a notice rather than
 * a broken form — dev contributors without an API key shouldn't be
 * blocked, they can bypass auth via the future `dev:auto-sign-in`
 * mechanism (not in this phase).
 */

import { redirect } from 'next/navigation';

import { auth, signIn } from '@/lib/auth';
import { serverEnv } from '@/lib/env';

interface PageProps {
  searchParams: Promise<{ callbackUrl?: string }>;
}

export const dynamic = 'force-dynamic';

export default async function SignInPage({ searchParams }: PageProps): Promise<React.ReactElement> {
  const userSession = await auth();
  const { callbackUrl } = await searchParams;
  if (userSession?.user) {
    redirect(callbackUrl ?? '/sessions');
  }

  const resendConfigured = serverEnv?.AUTH_RESEND_KEY !== undefined;

  async function submit(formData: FormData): Promise<void> {
    'use server';
    const raw = formData.get('email');
    // `FormData.get` returns `FormDataEntryValue` (string | File | null).
    // Narrow to string so we don't accidentally `"[object File]"` an
    // uploaded file into the email field.
    const email = typeof raw === 'string' ? raw.trim() : '';
    if (email.length === 0) return;
    await signIn('resend', { email, redirectTo: callbackUrl ?? '/sessions' });
  }

  return (
    <main className="bg-surface-secondary text-text-primary min-h-screen">
      <div className="mx-auto flex max-w-md flex-col gap-6 px-6 py-24">
        <header>
          <h1 className="text-2xl font-medium tracking-tight">Sign in</h1>
          <p className="text-text-secondary mt-1 text-sm">
            We&apos;ll email you a magic link — no password to remember.
          </p>
        </header>

        {resendConfigured ? (
          <form
            action={submit}
            className="border-border-default bg-surface-primary flex flex-col gap-4 rounded-lg border p-6"
          >
            <label className="flex flex-col gap-2 text-sm">
              <span className="text-text-primary font-medium">Work email</span>
              <input
                type="email"
                name="email"
                required
                autoComplete="email"
                placeholder="you@yourorg.com"
                className="border-border-default rounded-md border bg-transparent px-3 py-2 text-sm"
              />
            </label>
            <button
              type="submit"
              className="bg-text-primary text-surface-primary rounded-md px-4 py-2 text-sm font-medium hover:opacity-90"
            >
              Email me a link
            </button>
          </form>
        ) : (
          <div className="border-border-default bg-surface-primary rounded-lg border p-6 text-sm">
            <p>
              <strong>Email sign-in is not configured.</strong> Set{' '}
              <code className="font-mono text-xs">AUTH_RESEND_KEY</code> and{' '}
              <code className="font-mono text-xs">AUTH_EMAIL_FROM</code> in this environment to
              enable magic-link login.
            </p>
          </div>
        )}
      </div>
    </main>
  );
}
