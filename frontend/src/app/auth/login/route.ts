import { NextResponse, type NextRequest } from "next/server";

import { authConfig } from "@/lib/auth/config";
import { authorizationUrl } from "@/lib/auth/oidc";
import { TX_COOKIE, TX_TTL_SECONDS, randomToken, sanitizeReturnTo, signTx } from "@/lib/auth/utils";

// Starts the authorization-code + PKCE flow. state/nonce/verifier live in a signed, short-lived,
// httpOnly cookie scoped to the callback path, never in the URL.
export async function GET(req: NextRequest) {
  const c = authConfig();
  const tx = {
    state: randomToken(),
    nonce: randomToken(),
    verifier: randomToken(48),
    returnTo: sanitizeReturnTo(req.nextUrl.searchParams.get("returnTo")),
  };
  const res = NextResponse.redirect(authorizationUrl(tx.state, tx.nonce, tx.verifier), 303);
  res.cookies.set(TX_COOKIE, await signTx(tx, c.secret), {
    httpOnly: true,
    sameSite: "lax", // must accompany the top-level redirect back from Keycloak
    secure: c.appOrigin.startsWith("https://"),
    path: "/auth/callback",
    maxAge: TX_TTL_SECONDS,
  });
  return res;
}
