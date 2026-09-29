import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { ChatApp } from "@/components/chat/ChatApp";
import { cookieNameForApp, loadSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

// Server component: no session -> straight to the identity provider. Nothing about the user is
// trusted from the browser; the name shown comes from the verified ID token in the session.
export default async function Home() {
  const id = (await cookies()).get(cookieNameForApp())?.value;
  const session = await loadSession(id);
  if (!session) redirect("/auth/login?returnTo=/");
  return (
    <main>
      <ChatApp username={session.username} />
    </main>
  );
}
