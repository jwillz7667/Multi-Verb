interface PageProps {
  searchParams: Promise<{ error?: string }>;
}

export default async function SignInErrorPage({
  searchParams,
}: PageProps): Promise<React.ReactElement> {
  const { error } = await searchParams;
  return (
    <main className="bg-surface-secondary text-text-primary min-h-screen">
      <div className="mx-auto flex max-w-md flex-col gap-4 px-6 py-24">
        <h1 className="text-2xl font-medium tracking-tight">Sign-in failed</h1>
        <p className="text-text-secondary text-sm">
          {error === undefined
            ? "We couldn't sign you in. Try requesting a new link."
            : `Auth.js reported: ${error}.`}
        </p>
        <a href="/sign-in" className="text-text-secondary text-sm underline">
          Try again
        </a>
      </div>
    </main>
  );
}
