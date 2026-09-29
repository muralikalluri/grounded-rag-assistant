import "server-only";

import { createRemoteJWKSet, jwtVerify } from "jose";

import { authConfig } from "@/lib/auth/config";
import { pkceChallenge } from "@/lib/auth/utils";

export type TokenResponse = {
  access_token: string;
  refresh_token?: string;
  id_token?: string;
  expires_in: number;
};

const OIDC = "/protocol/openid-connect";

/** Browser-facing URL (public issuer). */
export function authorizationUrl(state: string, nonce: string, verifier: string): string {
  const c = authConfig();
  const q = new URLSearchParams({
    client_id: c.clientId,
    redirect_uri: c.redirectUri, // a constant, never derived from the request
    response_type: "code",
    scope: "openid profile",
    state,
    nonce,
    code_challenge: pkceChallenge(verifier),
    code_challenge_method: "S256",
  });
  return `${c.issuer}${OIDC}/auth?${q}`;
}

export function endSessionUrl(idToken: string): string {
  const c = authConfig();
  const q = new URLSearchParams({
    id_token_hint: idToken,
    post_logout_redirect_uri: c.postLogoutRedirectUri, // fixed, from config
    client_id: c.clientId,
  });
  return `${c.issuer}${OIDC}/logout?${q}`;
}

/** A token-endpoint failure. Only the HTTP status is kept: response bodies can echo secrets. */
export class TokenError extends Error {
  constructor(readonly status: number) {
    super(`token endpoint returned ${status}`);
  }
  /** 400/401 = the grant itself is dead (revoked / reused / expired); anything else is transient. */
  get isInvalidGrant(): boolean {
    return this.status === 400 || this.status === 401;
  }
}

async function tokenRequest(body: Record<string, string>): Promise<TokenResponse> {
  const c = authConfig();
  const res = await fetch(`${c.internal}${OIDC}/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: c.clientId, client_secret: c.clientSecret, ...body }),
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
  });
  if (!res.ok) throw new TokenError(res.status);
  return (await res.json()) as TokenResponse;
}

export function exchangeCode(code: string, verifier: string): Promise<TokenResponse> {
  return tokenRequest({
    grant_type: "authorization_code",
    code,
    redirect_uri: authConfig().redirectUri,
    code_verifier: verifier,
  });
}

export function refreshTokens(refreshToken: string): Promise<TokenResponse> {
  return tokenRequest({ grant_type: "refresh_token", refresh_token: refreshToken });
}

type Jwks = ReturnType<typeof createRemoteJWKSet>;
const jwksCache = new Map<string, Jwks>();

function jwks(): Jwks {
  const url = `${authConfig().internal}${OIDC}/certs`; // fetched server-to-server
  let j = jwksCache.get(url);
  if (!j) {
    j = createRemoteJWKSet(new URL(url), { cooldownDuration: 30_000, cacheMaxAge: 600_000 });
    jwksCache.set(url, j);
  }
  return j;
}

/** Verify the ID token's signature, issuer, audience, expiry, nonce and azp. */
export async function verifyIdToken(idToken: string, nonce: string): Promise<{ username: string }> {
  const c = authConfig();
  const { payload } = await jwtVerify(idToken, jwks(), {
    issuer: c.issuer,
    audience: c.clientId,
    algorithms: ["RS256"],
  });
  if (payload.nonce !== nonce) throw new Error("nonce mismatch");
  if (payload.azp !== undefined && payload.azp !== c.clientId) throw new Error("azp mismatch");
  const name = payload.preferred_username;
  return { username: typeof name === "string" && name ? name : String(payload.sub) };
}
