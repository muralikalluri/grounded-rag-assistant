import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

// ---- an in-memory Redis with the bits of SET NX / EX we rely on ---------------------------
class FakeRedis {
  store = new Map<string, string>();
  on() {
    return this;
  }
  async connect() {}
  async get(k: string) {
    return this.store.get(k) ?? null;
  }
  async set(k: string, v: string, opts?: { NX?: boolean; EX?: number }) {
    if (opts?.NX && this.store.has(k)) return null;
    this.store.set(k, v);
    return "OK";
  }
  async del(k: string) {
    return this.store.delete(k) ? 1 : 0;
  }
}
const fake = new FakeRedis();
vi.mock("redis", () => ({ createClient: () => fake }));
vi.mock("@/lib/auth/config", () => ({
  authConfig: () => ({ redisUrl: "redis://x", appOrigin: "http://localhost:3000" }),
}));
const refreshTokens = vi.fn();
vi.mock("@/lib/auth/oidc", () => {
  class TokenError extends Error {
    constructor(readonly status: number) {
      super(`token endpoint returned ${status}`);
    }
    get isInvalidGrant() {
      return this.status === 400 || this.status === 401;
    }
  }
  return { TokenError, refreshTokens: (...a: unknown[]) => refreshTokens(...a) };
});

import { TokenError } from "@/lib/auth/oidc";
import { createSession, freshSession, loadSession, type Session } from "./session";

const now = () => Math.floor(Date.now() / 1000);
const base = (over: Partial<Session> = {}): Session => ({
  accessToken: "AT0", refreshToken: "RT0", idToken: "IDT0", expiresAt: now() + 3600, username: "alice", ...over,
});
const tokens = (n: number) => ({ access_token: `AT${n}`, refresh_token: `RT${n}`, id_token: `IDT${n}`, expires_in: 300 });

beforeEach(() => {
  fake.store.clear();
  refreshTokens.mockReset();
});

describe("freshSession", () => {
  it("returns a still-valid session untouched, without contacting the identity provider", async () => {
    const id = await createSession(base());
    expect((await freshSession(id))?.accessToken).toBe("AT0");
    expect(refreshTokens).not.toHaveBeenCalled();
  });

  it.each([undefined, "", "short", "x".repeat(200), "unknown-session-id-that-is-long-enough"])(
    "returns null for a missing or unknown session id (%j)",
    async (id) => {
      expect(await freshSession(id)).toBeNull();
    },
  );

  it("refreshes an expiring session, stores the ROTATED refresh token and releases the lock", async () => {
    const id = await createSession(base({ expiresAt: now() + 20 }));
    refreshTokens.mockResolvedValue(tokens(1));
    const s = await freshSession(id);
    expect(refreshTokens).toHaveBeenCalledWith("RT0");
    expect(s).toMatchObject({ accessToken: "AT1", refreshToken: "RT1", idToken: "IDT1", username: "alice" });
    expect((await loadSession(id))?.refreshToken).toBe("RT1"); // persisted
    expect(fake.store.has(`lock:${id}`)).toBe(false);
    // next call sees a fresh token and does not refresh again
    await freshSession(id);
    expect(refreshTokens).toHaveBeenCalledOnce();
  });

  it("keeps the old refresh token when the provider does not rotate it", async () => {
    const id = await createSession(base({ expiresAt: now() + 20 }));
    refreshTokens.mockResolvedValue({ access_token: "AT1", expires_in: 300 });
    expect((await freshSession(id))?.refreshToken).toBe("RT0");
  });

  it("destroys the session when the grant is dead (revoked / reused refresh token)", async () => {
    const id = await createSession(base({ expiresAt: now() + 20 }));
    refreshTokens.mockRejectedValue(new TokenError(400));
    expect(await freshSession(id)).toBeNull();
    expect(await loadSession(id)).toBeNull();
    expect(fake.store.has(`lock:${id}`)).toBe(false);
  });

  it("does NOT sign the user out on a transient identity-provider failure", async () => {
    const id = await createSession(base({ expiresAt: now() + 20 }));
    refreshTokens.mockRejectedValue(new TokenError(503));
    expect((await freshSession(id))?.accessToken).toBe("AT0"); // still usable for a few seconds
    expect(await loadSession(id)).not.toBeNull(); // session kept
    refreshTokens.mockRejectedValue(new Error("network down"));
    expect(await freshSession(id)).not.toBeNull();
    // ... but an access token that has really expired cannot be used
    const dead = await createSession(base({ expiresAt: now() - 5 }));
    expect(await freshSession(dead)).toBeNull();
    expect(await loadSession(dead)).not.toBeNull(); // still not destroyed: a later refresh may succeed
  });

  it("never spends the same refresh token twice when requests race", async () => {
    const id = await createSession(base({ expiresAt: now() + 20 }));
    refreshTokens.mockImplementation(async () => {
      await new Promise((r) => setTimeout(r, 150)); // a slowish token endpoint
      return tokens(1);
    });
    const results = await Promise.all([freshSession(id), freshSession(id), freshSession(id)]);
    expect(refreshTokens).toHaveBeenCalledOnce();
    expect(results.map((s) => s?.accessToken)).toEqual(["AT1", "AT1", "AT1"]); // losers got the winner's result
  });

  it("does not release a lock it no longer owns", async () => {
    const id = await createSession(base({ expiresAt: now() + 20 }));
    refreshTokens.mockImplementation(async () => {
      fake.store.set(`lock:${id}`, "another-owner"); // our lock expired and someone else took it
      return tokens(1);
    });
    await freshSession(id);
    expect(fake.store.get(`lock:${id}`)).toBe("another-owner");
  });
});
