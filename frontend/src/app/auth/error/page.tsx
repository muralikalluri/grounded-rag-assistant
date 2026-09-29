import { Card, CardContent } from "@/components/ui/card";

// Server component. The reason code is looked up, never rendered as provided.
const MESSAGES: Record<string, string> = {
  expired: "Your sign-in attempt expired. Please try again.",
  denied: "Sign-in was cancelled or denied.",
  state: "The sign-in response did not match the request. Please try again.",
  tokens: "The identity provider did not return the expected tokens.",
  failed: "Sign-in failed. Please try again.",
};

export default async function AuthError({ searchParams }: { searchParams: Promise<{ reason?: string }> }) {
  const { reason } = await searchParams;
  // hasOwn: `?reason=__proto__` / `constructor` must not reach Object.prototype members.
  const message = reason && Object.hasOwn(MESSAGES, reason) ? MESSAGES[reason] : MESSAGES.failed;
  return (
    <main className="mx-auto max-w-md px-4 py-16">
      <Card>
        <CardContent className="space-y-3">
          <h1 className="text-lg font-semibold">Sign-in problem</h1>
          <p className="text-sm text-neutral-700" data-testid="auth-error">
            {message}
          </p>
          <a className="text-sm text-blue-700 underline" href="/auth/login">
            Try again
          </a>
        </CardContent>
      </Card>
    </main>
  );
}
