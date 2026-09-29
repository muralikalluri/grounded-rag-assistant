// Pure helpers for the OIDC login flow (unit tested; no I/O).
import { createHash, randomBytes } from "node:crypto";
import { SignJWT, jwtVerify } from "jose";

export const SESSION_COOKIE = "session";
export const SECURE_SESSION_COOKIE = "__Host-session"; // requires Secure + Path=/ (https only)
export const TX_COOKIE = "oidc_tx";
export const TX_TTL_SECONDS = 300;

export function randomToken(bytes = 32): string {
  return randomBytes(bytes).toString("base64url");
}

/** PKCE S256 challenge for a verifier. */
export function pkceChallenge(verifier: string): string {
  return createHash("sha256").update(verifier).digest("base64url");
}

/** The cookie name depends on whether the app is served over https. */
export function sessionCookieName(appOrigin: string): string {
  return appOrigin.startsWith("https://") ? SECURE_SESSION_COOKIE : SESSION_COOKIE;
}

/**
 * Only a same-site relative path may be used as a post-login destination. Anything that could
 * leave the site ("//evil", "/\evil", absolute URLs, control characters) falls back to "/".
 */
export function sanitizeReturnTo(value: string | null | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return "/";
  if (/[\u0000-\u001f\u007f\\]/.test(value)) return "/";
  return value.length > 512 ? "/" : value;
}

/** CSRF: state-changing requests must come from our own origin. */
export function sameOrigin(req: { headers: { get(name: string): string | null } }, appOrigin: string): boolean {
  const origin = req.headers.get("origin");
  if (origin) return origin === appOrigin;
  // Some browsers omit Origin on same-origin GET-like navigations; POSTs always send it. Fall
  // back to Sec-Fetch-Site, and otherwise refuse.
  return req.headers.get("sec-fetch-site") === "same-origin";
}

export type Tx = { state: string; nonce: string; verifier: string; returnTo: string };

const enc = (secret: string) => new TextEncoder().encode(secret);

/** The login transaction lives in a short-lived, signed, httpOnly cookie (not in the URL). */
export async function signTx(tx: Tx, secret: string): Promise<string> {
  return new SignJWT({ ...tx })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime(`${TX_TTL_SECONDS}s`)
    .sign(enc(secret));
}

export async function verifyTx(token: string | undefined, secret: string): Promise<Tx | null> {
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, enc(secret), { algorithms: ["HS256"] });
    const { state, nonce, verifier, returnTo } = payload as Record<string, unknown>;
    if ([state, nonce, verifier, returnTo].some((v) => typeof v !== "string")) return null;
    return { state, nonce, verifier, returnTo } as Tx;
  } catch {
    return null;
  }
}
