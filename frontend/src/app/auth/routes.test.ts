import { NextRequest, type NextResponse } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const CFG = {
  appOrigin: "http://localhost:3000",
  secret: "a-test-secret-that-is-at-least-32-chars-long",
  issuer: "http://localhost:8080/realms/northwind",
  clientId: "rag-web",
};
vi.mock("@/lib/auth/config", () => ({ authConfig: () => CFG }));

const exchangeCode = vi.fn();
const verifyIdToken = vi.fn();
vi.mock("@/lib/auth/oidc", async () => {
  const real = await vi.importActual<typeof import("@/lib/auth/utils")>("@/lib/auth/utils");
  return {
    authorizationUrl: (state: string, nonce: string, verifier: string) =>
      `${CFG.issuer}/protocol/openid-connect/auth?${new URLSearchParams({
        state, nonce, code_challenge: real.pkceChallenge(verifier), code_challenge_method: "S256",
        redirect_uri: `${CFG.appOrigin}/auth/callback`, client_id: CFG.clientId,
      })}`,
    endSessionUrl: (idToken: string) => `${CFG.issuer}/protocol/openid-connect/logout?id_token_hint=${idToken}`,
    exchangeCode: (...a: unknown[]) => exchangeCode(...a),
    verifyIdToken: (...a: unknown[]) => verifyIdToken(...a),
  };
});

const createSession = vi.fn();
const loadSession = vi.fn();
const destroySession = vi.fn();
vi.mock("@/lib/auth/session", () => ({
  cookieNameForApp: () => "session",
  createSession: (...a: unknown[]) => createSession(...a),
  loadSession: (...a: unknown[]) => loadSession(...a),
  destroySession: (...a: unknown[]) => destroySession(...a),
}));

import { GET as callback } from "./callback/route";
import { GET as login } from "./login/route";
import { POST as logout } from "./logout/route";
import { TX_COOKIE, signTx } from "@/lib/auth/utils";

beforeEach(() => {
  for (const m of [exchangeCode, verifyIdToken, createSession, loadSession, destroySession]) m.mockReset();
});

const nreq = (url: string, init?: ConstructorParameters<typeof NextRequest>[1]) => new NextRequest(url, init);

describe("/auth/login", () => {
  it("redirects to the IdP with PKCE S256 and puts state/nonce/verifier in a signed httpOnly cookie", async () => {
    const res = await login(nreq("http://localhost:3000/auth/login?returnTo=/docs"));
    expect(res.status).toBe(303);
    const loc = new URL(res.headers.get("location")!);
    expect(loc.origin + loc.pathname).toBe(`${CFG.issuer}/protocol/openid-connect/auth`);
    expect(loc.searchParams.get("code_challenge_method")).toBe("S256");
    expect(loc.searchParams.get("state")).toBeTruthy();
    expect(loc.searchParams.get("nonce")).toBeTruthy();
    expect(loc.searchParams.get("code_verifier")).toBeNull(); // the verifier never leaves the server
    const cookie = res.cookies.get(TX_COOKIE)!;
    expect(cookie.value.split(".")).toHaveLength(3);
    expect(cookie.httpOnly).toBe(true);
    expect(cookie.path).toBe("/auth/callback");
    expect(cookie.sameSite).toBe("lax");
    expect(res.headers.get("location")).not.toContain("returnTo"); // destination stays in the cookie
  });

  it("neutralises an open-redirect returnTo", async () => {
    const res = await login(nreq("http://localhost:3000/auth/login?returnTo=//evil.example"));
    const { verifyTx } = await import("@/lib/auth/utils");
    expect((await verifyTx(res.cookies.get(TX_COOKIE)!.value, CFG.secret))!.returnTo).toBe("/");
  });
});

describe("/auth/callback", () => {
  const tx = { state: "STATE1", nonce: "NONCE1", verifier: "VERIFIER1", returnTo: "/docs" };
  async function call(query: string, opts: { cookie?: string | null } = {}) {
    const cookie = opts.cookie === undefined ? await signTx(tx, CFG.secret) : opts.cookie;
    return callback(
      nreq(`http://localhost:3000/auth/callback?${query}`, { headers: cookie ? { cookie: `${TX_COOKIE}=${cookie}` } : {} }),
    );
  }
  const tokens = { access_token: "AT", refresh_token: "RT", id_token: "IDT", expires_in: 300 };

  it("creates a session, sets an httpOnly cookie, clears the transaction and goes to returnTo", async () => {
    exchangeCode.mockResolvedValue(tokens);
    verifyIdToken.mockResolvedValue({ username: "alice" });
    createSession.mockResolvedValue("SESSIONID");
    const res = await call("code=abc&state=STATE1");
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("http://localhost:3000/docs");
    expect(exchangeCode).toHaveBeenCalledWith("abc", "VERIFIER1");
    expect(verifyIdToken).toHaveBeenCalledWith("IDT", "NONCE1");
    const s = res.cookies.get("session")!;
    expect(s.value).toBe("SESSIONID");
    expect(s.httpOnly).toBe(true);
    expect(s.sameSite).toBe("lax");
    expect(createSession.mock.calls[0]![0]).toMatchObject({ accessToken: "AT", username: "alice" });
    expect(JSON.stringify(res.headers.get("location"))).not.toContain("AT");
  });

  it.each([
    ["missing transaction cookie", "code=abc&state=STATE1", null],
    ["tampered transaction cookie", "code=abc&state=STATE1", "x.y.z"],
    ["state mismatch (CSRF / mix-up)", "code=abc&state=OTHER", undefined],
    ["IdP error response", "error=access_denied&state=STATE1", undefined],
    ["no code", "state=STATE1", undefined],
  ])("refuses: %s", async (_n, query, cookie) => {
    const res = await call(query, { cookie });
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toContain("/auth/error?reason=");
    expect(exchangeCode).not.toHaveBeenCalled();
    expect(createSession).not.toHaveBeenCalled();
    expect(res.cookies.get("session")).toBeUndefined();
  });

  it("refuses when the code exchange or ID-token verification fails, without leaking the reason", async () => {
    exchangeCode.mockRejectedValue(new Error("token endpoint returned 400"));
    let res = await call("code=abc&state=STATE1");
    expect(res.headers.get("location")).toBe("http://localhost:3000/auth/error?reason=failed");
    exchangeCode.mockResolvedValue(tokens);
    verifyIdToken.mockRejectedValue(new Error("nonce mismatch"));
    res = await call("code=abc&state=STATE1");
    expect(res.headers.get("location")).toBe("http://localhost:3000/auth/error?reason=failed");
    expect(createSession).not.toHaveBeenCalled();
  });

  it("refuses a token response without a refresh or ID token", async () => {
    exchangeCode.mockResolvedValue({ access_token: "AT", expires_in: 300 });
    const res = await call("code=abc&state=STATE1");
    expect(res.headers.get("location")).toContain("reason=tokens");
    expect(createSession).not.toHaveBeenCalled();
  });

  it("never redirects outside the site even if a bad returnTo somehow reached the cookie", async () => {
    exchangeCode.mockResolvedValue(tokens);
    verifyIdToken.mockResolvedValue({ username: "alice" });
    createSession.mockResolvedValue("SID");
    const evil = await signTx({ ...tx, returnTo: "//evil.example/x" }, CFG.secret);
    const res = await call("code=abc&state=STATE1", { cookie: evil });
    expect(res.headers.get("location")).toBe("http://localhost:3000/");
  });
});

describe("/auth/logout", () => {
  const post = (headers: Record<string, string>) =>
    logout(nreq("http://localhost:3000/auth/logout", { method: "POST", headers: { cookie: "session=SID123", ...headers } }));

  it("destroys the session server-side, clears the cookie and ends the IdP session", async () => {
    loadSession.mockResolvedValue({ idToken: "IDT" });
    const res = await post({ origin: "http://localhost:3000" });
    expect(destroySession).toHaveBeenCalledWith("SID123");
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toContain("/protocol/openid-connect/logout?id_token_hint=IDT");
    expect((res as NextResponse).cookies.get("session")?.value).toBe("");
  });

  it("refuses cross-origin logout requests (no forced sign-out from another site)", async () => {
    expect((await post({ origin: "https://evil.example" })).status).toBe(403);
    expect((await post({})).status).toBe(403);
    expect(destroySession).not.toHaveBeenCalled();
  });

  it("still clears the local cookie when the session is already gone", async () => {
    loadSession.mockResolvedValue(null);
    const res = await post({ origin: "http://localhost:3000" });
    expect(res.headers.get("location")).toBe("http://localhost:3000/");
  });
});
