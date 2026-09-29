import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const freshSession = vi.fn();
vi.mock("@/lib/auth/config", () => ({
  authConfig: () => ({ appOrigin: "http://localhost:3000" }),
}));
vi.mock("@/lib/auth/session", () => ({
  cookieNameForApp: () => "session",
  freshSession: (...a: unknown[]) => freshSession(...a),
}));

import { GET, POST } from "./[...path]/route";

const UUID = "3ffd877d-0000-4000-8000-000000000001";
const ctx = (path: string) => ({ params: Promise.resolve({ path: path.split("/") }) });

function req(path: string, init: { method?: string; headers?: Record<string, string>; body?: string } = {}) {
  return new NextRequest(`http://localhost:3000/api/${path}`, {
    method: init.method ?? "GET",
    headers: init.headers,
    body: init.body,
  } as ConstructorParameters<typeof NextRequest>[1]);
}

let upstream: ReturnType<typeof vi.fn>;
beforeEach(() => {
  upstream = vi.fn(
    async () =>
      new Response("ok", {
        status: 200,
        headers: { "content-type": "image/png", "set-cookie": "x=1", "x-secret": "no" },
      }),
  );
  vi.stubGlobal("fetch", upstream);
  freshSession.mockReset();
  freshSession.mockResolvedValue({ accessToken: "ACCESS-TOKEN-1", username: "alice" });
});
afterEach(() => vi.unstubAllGlobals());

const CHAT = { method: "POST", body: JSON.stringify({ question: "hi" }) };
const CSRF_OK = { "content-type": "application/json", origin: "http://localhost:3000", "x-requested-with": "fetch" };

describe("allowlist", () => {
  it.each([
    "health", "ready", "admin/metrics", "collections", "documents", `documents/${UUID}`,
    `documents/${UUID}/chunks`, `documents/${UUID}/chunks/not-a-uuid`,
    `documents/${UUID}/chunks/${UUID}/other`, `documents/${UUID}/chunks/${UUID}/page/extra`,
    "documents/../health", "chat/../health", "chat/extra",
  ])("does not forward %s (and never even looks up a session)", async (path) => {
    expect((await GET(req(path), ctx(path))).status).toBe(404);
    expect(upstream).not.toHaveBeenCalled();
    expect(freshSession).not.toHaveBeenCalled();
  });

  it.each([`documents/${UUID}/chunks/${UUID}`, `documents/${UUID}/chunks/${UUID}/locate`, `documents/${UUID}/chunks/${UUID}/page`])(
    "forwards GET %s for a signed-in user",
    async (path) => {
      expect((await GET(req(path), ctx(path))).status).toBe(200);
      expect(upstream).toHaveBeenCalledOnce();
    },
  );
});

describe("authentication", () => {
  it("answers 401 and never contacts the API without a valid session", async () => {
    freshSession.mockResolvedValue(null);
    const res = await GET(req(`documents/${UUID}/chunks/${UUID}/page`), ctx(`documents/${UUID}/chunks/${UUID}/page`));
    expect(res.status).toBe(401);
    expect(upstream).not.toHaveBeenCalled();
  });

  it("looks the session up from the session cookie only", async () => {
    await GET(req(`documents/${UUID}/chunks/${UUID}`, { headers: { cookie: "session=SID123; other=x" } }), ctx(`documents/${UUID}/chunks/${UUID}`));
    expect(freshSession).toHaveBeenCalledWith("SID123");
  });

  it("sends the user's access token upstream and drops every client-supplied credential", async () => {
    const res = await GET(
      req(`documents/${UUID}/chunks/${UUID}/page?page=4`, {
        headers: { authorization: "Bearer FORGED", "x-dev-user": "admin", cookie: "session=SID123; other=secret" },
      }),
      ctx(`documents/${UUID}/chunks/${UUID}/page`),
    );
    const [url, init] = upstream.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/documents/");
    expect(url).toContain("?page=4");
    const sent = new Headers(init.headers);
    expect(sent.get("authorization")).toBe("Bearer ACCESS-TOKEN-1");
    expect(sent.get("x-dev-user")).toBeNull();
    expect(sent.get("cookie")).toBeNull();
    expect(res.headers.get("content-type")).toBe("image/png");
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(res.headers.get("x-secret")).toBeNull();
  });
});

describe("CSRF and size limits on state-changing requests", () => {
  it.each([
    ["no Origin header", { "content-type": "application/json", "x-requested-with": "fetch" }],
    ["foreign Origin", { ...CSRF_OK, origin: "https://evil.example" }],
    ["missing X-Requested-With", { "content-type": "application/json", origin: "http://localhost:3000" }],
    ["wrong X-Requested-With", { ...CSRF_OK, "x-requested-with": "XMLHttpRequest" }],
  ])("refuses a POST with %s before touching the session or the API", async (_name, headers) => {
    const res = await POST(req("chat", { ...CHAT, headers }), ctx("chat"));
    expect(res.status).toBe(403);
    expect(freshSession).not.toHaveBeenCalled();
    expect(upstream).not.toHaveBeenCalled();
  });

  it("accepts a same-origin fetch POST", async () => {
    const res = await POST(req("chat", { ...CHAT, headers: CSRF_OK }), ctx("chat"));
    expect(res.status).toBe(200);
    const [, init] = upstream.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers).get("authorization")).toBe("Bearer ACCESS-TOKEN-1");
  });

  it("rejects oversized bodies before contacting the API", async () => {
    const big = JSON.stringify({ question: "x".repeat(20_000) });
    const res = await POST(req("chat", { method: "POST", body: big, headers: CSRF_OK }), ctx("chat"));
    expect(res.status).toBe(413);
    expect(upstream).not.toHaveBeenCalled();
  });

  it("answers 502 when the API is down", async () => {
    upstream.mockRejectedValueOnce(new Error("down"));
    expect((await GET(req(`documents/${UUID}/chunks/${UUID}`), ctx(`documents/${UUID}/chunks/${UUID}`))).status).toBe(502);
  });
});
