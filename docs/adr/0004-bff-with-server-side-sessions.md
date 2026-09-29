# 4. Next.js as a backend-for-frontend with server-side sessions

Status: accepted

**Decision.** The browser never holds tokens. Next.js performs the OIDC code+PKCE flow, keeps tokens in
Redis behind an opaque httpOnly cookie, and proxies an allow-list of API routes with the bearer token.

**Consequences.** No CORS, no tokens in JavaScript, and `<img>` can load ACL-protected page images.
Cost: a stateful web tier (Redis) and CSRF handling in the proxy.
