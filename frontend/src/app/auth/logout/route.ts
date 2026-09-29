import { NextResponse, type NextRequest } from "next/server";

import { authConfig } from "@/lib/auth/config";
import { endSessionUrl } from "@/lib/auth/oidc";
import { cookieNameForApp, destroySession, loadSession } from "@/lib/auth/session";
import { sameOrigin } from "@/lib/auth/utils";

// POST only (a GET link could be triggered by any page), and only from our own origin.
export async function POST(req: NextRequest) {
  const c = authConfig();
  if (!sameOrigin(req, c.appOrigin)) return new Response("Forbidden", { status: 403 });
  const name = cookieNameForApp();
  const id = req.cookies.get(name)?.value;
  const session = await loadSession(id);
  await destroySession(id);
  const target = session ? endSessionUrl(session.idToken) : `${c.appOrigin}/`;
  const res = NextResponse.redirect(target, 303);
  res.cookies.delete({ name, path: "/" });
  return res;
}
