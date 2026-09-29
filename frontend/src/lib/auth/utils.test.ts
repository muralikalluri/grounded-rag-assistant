import { SignJWT } from "jose";
import { describe, expect, it } from "vitest";

import {
  pkceChallenge,
  randomToken,
  sameOrigin,
  sanitizeReturnTo,
  sessionCookieName,
  signTx,
  verifyTx,
  type Tx,
} from "./utils";

const SECRET = "a-test-secret-that-is-at-least-32-chars-long";
const TX: Tx = { state: "s", nonce: "n", verifier: "v", returnTo: "/docs" };
const headers = (h: Record<string, string>) => ({ headers: { get: (k: string) => h[k.toLowerCase()] ?? null } });

describe("pkce", () => {
  it("matches the RFC 7636 appendix B test vector", () => {
    expect(pkceChallenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe(
      "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    );
  });
  it("generates unpredictable url-safe tokens", () => {
    const a = randomToken(), b = randomToken();
    expect(a).not.toBe(b);
    expect(a).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });
});

describe("sanitizeReturnTo (open-redirect guard)", () => {
  it.each([
    ["/", "/"], ["/docs", "/docs"], ["/a/b?c=d", "/a/b?c=d"],
    [null, "/"], [undefined, "/"], ["", "/"],
    ["//evil.example", "/"], ["/\\evil.example", "/"], ["https://evil.example", "/"],
    ["javascript:alert(1)", "/"], ["evil", "/"], ["/ok\nSet-Cookie: x=1", "/"], ["/a\\b", "/"],
    ["/" + "a".repeat(600), "/"],
  ])("%j -> %j", (input, expected) => {
    expect(sanitizeReturnTo(input as string | null | undefined)).toBe(expected);
  });
});

describe("sameOrigin (CSRF)", () => {
  const app = "http://localhost:3000";
  it("accepts only our own Origin", () => {
    expect(sameOrigin(headers({ origin: app }), app)).toBe(true);
    expect(sameOrigin(headers({ origin: "https://evil.example" }), app)).toBe(false);
    expect(sameOrigin(headers({ origin: "http://localhost:3000.evil.example" }), app)).toBe(false);
    expect(sameOrigin(headers({ origin: "null" }), app)).toBe(false);
  });
  it("falls back to Sec-Fetch-Site only when there is no Origin, and otherwise refuses", () => {
    expect(sameOrigin(headers({ "sec-fetch-site": "same-origin" }), app)).toBe(true);
    expect(sameOrigin(headers({ "sec-fetch-site": "cross-site" }), app)).toBe(false);
    expect(sameOrigin(headers({}), app)).toBe(false);
  });
});

describe("session cookie name", () => {
  it("uses the __Host- prefix only over https", () => {
    expect(sessionCookieName("https://app.example")).toBe("__Host-session");
    expect(sessionCookieName("http://localhost:3000")).toBe("session");
  });
});

describe("login transaction cookie", () => {
  it("round-trips", async () => {
    expect(await verifyTx(await signTx(TX, SECRET), SECRET)).toEqual(TX);
  });
  it("rejects tampering, a wrong secret, garbage and absence", async () => {
    const good = await signTx(TX, SECRET);
    const [h, p, s] = good.split(".");
    const forgedPayload = Buffer.from(JSON.stringify({ ...TX, state: "attacker" })).toString("base64url");
    expect(await verifyTx(`${h}.${forgedPayload}.${s}`, SECRET)).toBeNull();
    expect(await verifyTx(good, "another-secret-that-is-also-32-chars-long!")).toBeNull();
    expect(await verifyTx("not-a-jwt", SECRET)).toBeNull();
    expect(await verifyTx(undefined, SECRET)).toBeNull();
    expect(p).toBeTruthy();
  });
  it("rejects expired and incomplete transactions", async () => {
    const key = new TextEncoder().encode(SECRET);
    const expired = await new SignJWT({ ...TX }).setProtectedHeader({ alg: "HS256" }).setExpirationTime(Math.floor(Date.now() / 1000) - 10).sign(key);
    expect(await verifyTx(expired, SECRET)).toBeNull();
    const partial = await new SignJWT({ state: "s" }).setProtectedHeader({ alg: "HS256" }).setExpirationTime("5m").sign(key);
    expect(await verifyTx(partial, SECRET)).toBeNull();
  });
  it("refuses tokens signed with a different algorithm", async () => {
    const none = `${Buffer.from('{"alg":"none"}').toString("base64url")}.${Buffer.from(JSON.stringify(TX)).toString("base64url")}.`;
    expect(await verifyTx(none, SECRET)).toBeNull();
  });
});
