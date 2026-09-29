import { NextResponse, type NextRequest } from "next/server";

import { authConfig } from "@/lib/auth/config";
import { exchangeCode, verifyIdToken } from "@/lib/auth/oidc";
import { cookieNameForApp, createSession } from "@/lib/auth/session";
import { TX_COOKIE, sanitizeReturnTo, verifyTx } from "@/lib/auth/utils";

function fail(appOrigin: string, code: string) {
  // A generic destination: no provider error text is reflected back into the page.
  const res = NextResponse.redirect(`${appOrigin}/auth/error?reason=${code}`, 303);
  res.cookies.delete({ name: TX_COOKIE, path: "/auth/callback" });
  return res;
}

export async function GET(req: NextRequest) {
  const c = authConfig();
  const tx = await verifyTx(req.cookies.get(TX_COOKIE)?.value, c.secret);
  const q = req.nextUrl.searchParams;
  if (!tx) return fail(c.appOrigin, "expired");
  if (q.get("error") || !q.get("code")) return fail(c.appOrigin, "denied");
  if (q.get("state") !== tx.state) return fail(c.appOrigin, "state");

  try {
    const t = await exchangeCode(q.get("code")!, tx.verifier);
    if (!t.id_token || !t.refresh_token) return fail(c.appOrigin, "tokens");
    const { username } = await verifyIdToken(t.id_token, tx.nonce);
    const id = await createSession({
      accessToken: t.access_token,
      refreshToken: t.refresh_token,
      idToken: t.id_token,
      expiresAt: Math.floor(Date.now() / 1000) + t.expires_in,
      username,
    });
    const res = NextResponse.redirect(`${c.appOrigin}${sanitizeReturnTo(tx.returnTo)}`, 303);
    res.cookies.set(cookieNameForApp(), id, {
      httpOnly: true,
      sameSite: "lax",
      secure: c.appOrigin.startsWith("https://"),
      path: "/",
    });
    res.cookies.delete({ name: TX_COOKIE, path: "/auth/callback" }); // single use
    return res;
  } catch {
    return fail(c.appOrigin, "failed");
  }
}
