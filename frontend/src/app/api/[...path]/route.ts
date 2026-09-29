import type { NextRequest } from "next/server";

import { authConfig } from "@/lib/auth/config";
import { cookieNameForApp, freshSession } from "@/lib/auth/session";
import { sameOrigin } from "@/lib/auth/utils";

// Server-side proxy to the backend API. The browser only talks to this origin: no CORS, no
// tokens in JavaScript, and <img src> can load ACL-protected page images. The session cookie
// (opaque id) is exchanged here for the user's access token, which is the ONLY credential the
// API sees. Only the routes the UI needs are forwarded (no generic pass-through).
const API_URL = process.env.API_URL ?? "http://localhost:8000";
const ALLOWED = [/^chat$/, /^documents\/[0-9a-f-]{36}\/chunks\/[0-9a-f-]{36}(\/(locate|page))?$/];

// Questions are capped at 1000 characters by the backend; anything near this is not a chat request.
const MAX_BODY_BYTES = 16 * 1024;

// Response headers worth forwarding. Cache-Control is kept so `private, no-store` survives.
const PASS_HEADERS = ["content-type", "cache-control", "x-content-type-options", "content-disposition"];

const unauthenticated = () =>
  Response.json({ detail: "Authentication required" }, { status: 401, headers: { "cache-control": "no-store" } });

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const joined = path.join("/");
  if (!ALLOWED.some((rx) => rx.test(joined))) {
    return new Response("Not found", { status: 404 });
  }

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  if (hasBody) {
    // CSRF: SameSite=Lax already blocks cross-site POST cookies; also require our own origin and
    // a custom header (which forces a CORS preflight that we never answer).
    if (!sameOrigin(req, authConfig().appOrigin) || req.headers.get("x-requested-with") !== "fetch") {
      return new Response("Forbidden", { status: 403 });
    }
  }

  const session = await freshSession(req.cookies.get(cookieNameForApp())?.value);
  if (!session) return unauthenticated();

  // Build upstream headers from scratch: incoming Authorization / Cookie / X-Dev-User are dropped.
  const headers = new Headers({ authorization: `Bearer ${session.accessToken}` });
  let body: string | undefined;
  if (hasBody) {
    if (Number(req.headers.get("content-length") ?? 0) > MAX_BODY_BYTES) {
      return new Response("Payload too large", { status: 413 });
    }
    body = await req.text();
    if (new TextEncoder().encode(body).length > MAX_BODY_BYTES) {
      return new Response("Payload too large", { status: 413 });
    }
    headers.set("content-type", req.headers.get("content-type") ?? "application/json");
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API_URL}/api/${joined}${req.nextUrl.search}`, {
      method: req.method,
      headers,
      body,
      signal: req.signal, // client went away -> upstream request (and the LLM call) is cancelled
      cache: "no-store",
    });
  } catch {
    return Response.json({ detail: "API unavailable" }, { status: 502 });
  }
  const out = new Headers();
  for (const h of PASS_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  if (upstream.headers.get("content-type")?.startsWith("text/event-stream")) {
    out.set("cache-control", "no-cache, no-transform");
    out.set("x-accel-buffering", "no");
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export { proxy as GET, proxy as POST };
