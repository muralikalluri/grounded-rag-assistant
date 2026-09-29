import "server-only";

import { createClient, type RedisClientType } from "redis";

import { authConfig } from "@/lib/auth/config";
import { randomToken, sessionCookieName } from "@/lib/auth/utils";
import { TokenError, refreshTokens } from "@/lib/auth/oidc";

export type Session = {
  accessToken: string;
  refreshToken: string;
  idToken: string;
  /** epoch seconds when the access token expires */
  expiresAt: number;
  username: string;
};

const IDLE_TTL_SECONDS = 30 * 60; // matches the realm's SSO idle timeout
const REFRESH_MARGIN_SECONDS = 60;
const LOCK_TTL_SECONDS = 30; // longer than the token request's 10 s timeout
const LOCK_WAIT_MS = 3000;
const LOCK_POLL_MS = 100;

type Global = typeof globalThis & { __redis?: Promise<RedisClientType> };

function redis(): Promise<RedisClientType> {
  const g = globalThis as Global;
  g.__redis ??= (async () => {
    const client: RedisClientType = createClient({ url: authConfig().redisUrl });
    client.on("error", () => {}); // reconnects on its own; never log connection strings
    await client.connect();
    return client;
  })();
  return g.__redis;
}

const key = (id: string) => `sess:${id}`;

export async function createSession(data: Session): Promise<string> {
  const id = randomToken(32);
  await (await redis()).set(key(id), JSON.stringify(data), { EX: IDLE_TTL_SECONDS });
  return id;
}

export async function loadSession(id: string | undefined): Promise<Session | null> {
  if (!id || id.length < 20 || id.length > 100) return null;
  const raw = await (await redis()).get(key(id));
  return raw ? (JSON.parse(raw) as Session) : null;
}

export async function destroySession(id: string | undefined): Promise<void> {
  if (id) await (await redis()).del(key(id));
}

export function cookieNameForApp(): string {
  return sessionCookieName(authConfig().appOrigin);
}

/**
 * The session with a usable access token, refreshing it when it is about to expire.
 *
 * - Refresh-token rotation means two parallel requests must not both spend the same refresh
 *   token, so a Redis lock (with an owner token, so we never release someone else's) serialises
 *   them; waiting requests poll for the winner's result instead of giving up.
 * - Only a dead grant (400/401) destroys the session. A transient failure (network, 5xx) keeps
 *   it: the user is not signed out because Keycloak had a bad moment.
 */
export async function freshSession(id: string | undefined): Promise<Session | null> {
  const s = await loadSession(id);
  if (!s || !id) return null;
  const nowS = () => Math.floor(Date.now() / 1000);
  if (s.expiresAt - nowS() > REFRESH_MARGIN_SECONDS) return s;

  const r = await redis();
  const owner = randomToken(16);
  const locked = await r.set(`lock:${id}`, owner, { NX: true, EX: LOCK_TTL_SECONDS });
  if (!locked) {
    // Someone else is refreshing: wait for their result (or for the lock to clear).
    const deadline = Date.now() + LOCK_WAIT_MS;
    while (Date.now() < deadline) {
      await new Promise((res) => setTimeout(res, LOCK_POLL_MS));
      const again = await loadSession(id);
      if (!again) return null;
      if (again.expiresAt - nowS() > REFRESH_MARGIN_SECONDS) return again;
      if (!(await r.get(`lock:${id}`))) break;
    }
    const last = await loadSession(id);
    return last && last.expiresAt - nowS() > 0 ? last : null;
  }
  try {
    const t = await refreshTokens(s.refreshToken);
    const next: Session = {
      ...s,
      accessToken: t.access_token,
      refreshToken: t.refresh_token ?? s.refreshToken,
      idToken: t.id_token ?? s.idToken,
      expiresAt: nowS() + t.expires_in,
    };
    await r.set(key(id), JSON.stringify(next), { EX: IDLE_TTL_SECONDS });
    return next;
  } catch (err) {
    if (err instanceof TokenError && err.isInvalidGrant) {
      await destroySession(id);
      return null;
    }
    return s.expiresAt - nowS() > 0 ? s : null; // transient: keep the session, use what is left
  } finally {
    if ((await r.get(`lock:${id}`)) === owner) await r.del(`lock:${id}`);
  }
}
