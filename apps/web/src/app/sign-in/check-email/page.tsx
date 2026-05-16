export default function CheckEmailPage(): React.ReactElement {
  return (
    <main className="bg-surface-secondary text-text-primary min-h-screen">
      <div className="mx-auto flex max-w-md flex-col gap-4 px-6 py-24">
        <h1 className="text-2xl font-medium tracking-tight">Check your inbox</h1>
        <p className="text-text-secondary text-sm">
          We sent you a sign-in link. It expires in 24 hours. Open it on the device where you want
          to use Verbio.
        </p>
        <a href="/sign-in" className="text-text-secondary text-sm underline">
          Use a different email
        </a>
      </div>
    </main>
  );
}
