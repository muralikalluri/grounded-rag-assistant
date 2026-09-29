import { SignJWT } from "jose";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const ISSUER = "http://localhost:8080/realms/northwind";
vi.mock("server-only", () => ({}));
vi.mock("@/lib/auth/config", () => ({
  authConfig: () => ({
    issuer: ISSUER, internal: "http://keycloak:8080/realms/northwind", clientId: "rag-web",
    clientSecret: "SECRET-VALUE", appOrigin: "http://localhost:3000",
    redirectUri: "http://localhost:3000/auth/callback", postLogoutRedirectUri: "http://localhost:3000/",
  }),
}));

let priv: CryptoKey;
vi.mock("jose", async (orig) => {
  const actual = await orig<typeof import("jose")>();
  const pair = await actual.generateKeyPair("RS256");
  (globalThis as Record<string, unknown>).__priv = pair.privateKey;
  return { ...actual, createRemoteJWKSet: () => async () => pair.publicKey };
});

import { TokenError, authorizationUrl, endSessionUrl, exchangeCode, refreshTokens, verifyIdToken } from "./oidc";

beforeAll(() => {
  priv = (globalThis as Record<string, unknown>).__priv as CryptoKey;
});

async function idToken(over: Record<string, unknown> = {}, opts: { alg?: string; exp?: string | number; iss?: string; aud?: string } = {}) {
  const claims = { nonce: "N1", azp: "rag-web", preferred_username: "alice", sub: "sub-1", ...over };
  return new SignJWT(claims)
    .setProtectedHeader({ alg: opts.alg ?? "RS256" })
    .setIssuer(opts.iss ?? ISSUER)
    .setAudience(opts.aud ?? "rag-web")
    .setIssuedAt()
    .setExpirationTime(opts.exp ?? "5m")
    .sign(priv);
}

describe("verifyIdToken", () => {
  it("accepts a good token and returns the username", async () => {
    expect(await verifyIdToken(await idToken(), "N1")).toEqual({ username: "alice" });
  });
  it("falls back to sub when there is no preferred_username", async () => {
    expect(await verifyIdToken(await idToken({ preferred_username: undefined }), "N1")).toEqual({ username: "sub-1" });
  });
  it.each([
    ["wrong nonce (replay)", () => idToken({ nonce: "OTHER" }), "N1"],
    ["missing nonce", () => idToken({ nonce: undefined }), "N1"],
    ["wrong azp", () => idToken({ azp: "other-client" }), "N1"],
    ["wrong issuer", () => idToken({}, { iss: "http://evil.example/realms/x" }), "N1"],
    ["wrong audience", () => idToken({}, { aud: "some-other-client" }), "N1"],
    ["expired", () => idToken({}, { exp: Math.floor(Date.now() / 1000) - 3600 }), "N1"],
  ])("rejects: %s", async (_name, make, nonce) => {
    await expect(verifyIdToken(await make(), nonce)).rejects.toThrow();
  });
  it("rejects HS256 and unsigned tokens (algorithm pinned to RS256)", async () => {
    const hs = await new SignJWT({ nonce: "N1" }).setProtectedHeader({ alg: "HS256" }).setIssuer(ISSUER).setAudience("rag-web").setExpirationTime("5m").sign(new TextEncoder().encode("secret-secret-secret-secret-32b"));
    await expect(verifyIdToken(hs, "N1")).rejects.toThrow();
    const none = `${Buffer.from('{"alg":"none"}').toString("base64url")}.${Buffer.from(JSON.stringify({ iss: ISSUER, aud: "rag-web", nonce: "N1" })).toString("base64url")}.`;
    await expect(verifyIdToken(none, "N1")).rejects.toThrow();
  });
});

describe("token endpoint calls", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("sends the client secret and PKCE verifier in the body to the INTERNAL address", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ access_token: "a", expires_in: 300 })));
    vi.stubGlobal("fetch", f);
    await exchangeCode("CODE", "VERIFIER");
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://keycloak:8080/realms/northwind/protocol/openid-connect/token");
    const body = new URLSearchParams(init.body as string);
    expect(body.get("grant_type")).toBe("authorization_code");
    expect(body.get("code_verifier")).toBe("VERIFIER");
    expect(body.get("client_secret")).toBe("SECRET-VALUE");
    expect(body.get("redirect_uri")).toBe("http://localhost:3000/auth/callback");
  });

  it("reports only the status on failure and classifies dead grants", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response('{"error_description":"echoes SECRET-VALUE"}', { status: 400 })));
    const err = (await refreshTokens("RT").catch((e) => e)) as TokenError;
    expect(err).toBeInstanceOf(TokenError);
    expect(err.message).toBe("token endpoint returned 400");
    expect(err.message).not.toContain("SECRET");
    expect(err.isInvalidGrant).toBe(true);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));
    expect(((await refreshTokens("RT").catch((e) => e)) as TokenError).isInvalidGrant).toBe(false);
  });
});

describe("browser-facing URLs", () => {
  it("authorization URL uses the PUBLIC issuer, PKCE S256, and the configured redirect", () => {
    const u = new URL(authorizationUrl("S", "N", "V"));
    expect(u.origin + u.pathname).toBe(`${ISSUER}/protocol/openid-connect/auth`);
    expect(u.searchParams.get("code_challenge_method")).toBe("S256");
    expect(u.searchParams.get("redirect_uri")).toBe("http://localhost:3000/auth/callback");
    expect(u.searchParams.get("code_verifier")).toBeNull();
    expect(u.searchParams.get("client_secret")).toBeNull();
  });
  it("logout URL carries a fixed post-logout redirect", () => {
    const u = new URL(endSessionUrl("IDT"));
    expect(u.searchParams.get("post_logout_redirect_uri")).toBe("http://localhost:3000/");
    expect(u.searchParams.get("id_token_hint")).toBe("IDT");
  });
});
