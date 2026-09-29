import "server-only";

// Read lazily (per request) so `next build` does not need the runtime secrets.
function need(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`${name} is not set`);
  return v;
}

export function authConfig() {
  const appOrigin = need("APP_ORIGIN").replace(/\/$/, "");
  const secret = need("SESSION_SECRET");
  if (secret.length < 32) throw new Error("SESSION_SECRET must be at least 32 characters");
  const issuer = need("OIDC_ISSUER"); // public issuer: what browsers and tokens see
  const internal = (process.env.OIDC_INTERNAL_URL ?? issuer).replace(/\/$/, ""); // server-to-server
  return {
    appOrigin,
    secret,
    issuer,
    internal,
    clientId: process.env.OIDC_CLIENT_ID ?? "rag-web",
    clientSecret: need("OIDC_CLIENT_SECRET"),
    redisUrl: process.env.REDIS_URL ?? "redis://localhost:6379/0",
    redirectUri: `${appOrigin}/auth/callback`,
    postLogoutRedirectUri: `${appOrigin}/`,
  };
}
