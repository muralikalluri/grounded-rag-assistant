# Keycloak (local demo identity provider)

`realm-northwind.json` is imported by `docker compose` (`start-dev --import-realm`). It exists so
the demo runs on a fresh clone with real single sign-on. **It is for local compose only.**

## Demo users (fictional, throwaway passwords)

| User | Password | Groups | Roles | Can read |
|---|---|---|---|---|
| `alice` | `alice-demo-only` | HR | user | company + HR documents |
| `bob` | `bob-demo-only` | Engineering | user | company + engineering documents |
| `admin` | `admin-demo-only` | none | user, admin | **nothing by default** (the admin role grants operations, not document access) |

These passwords are committed on purpose because they only protect a throwaway local stack.
Never import this realm into a real environment, and never reuse these values anywhere.

## Secrets that are *not* in the export

- **Client secret** of `rag-web`: injected at import from `RAG_WEB_CLIENT_SECRET` (`.env`).
  To rotate it, change the variable and recreate the realm (see "Reset" below).
- **Keycloak admin console** login: `KEYCLOAK_ADMIN_USER` / `KEYCLOAK_ADMIN_PASSWORD` (`.env`).
- **Web session secret**: `SESSION_SECRET` (signs the short-lived login-transaction cookie).

## How tokens are validated

- The web app is a confidential OIDC client (authorization code + PKCE S256, state and nonce).
  Tokens stay server-side (Redis); the browser only holds an opaque httpOnly session cookie.
- The API accepts only RS256 access tokens whose `iss` is the public issuer, `aud` contains
  `rag-api`, `azp` is `rag-web` and `typ` is `Bearer`. ID tokens and refresh tokens are rejected.
- The `groups` claim uses `full.path=false` (`HR`, not `/HR`) and is matched *exactly* against
  collection ACL group names. Keycloak omits the claim for users without groups; that means "no
  memberships", i.e. no access.
- Group changes take effect at the next token: up to the access-token lifetime (5 min) plus the
  chat stream cap.

## Hostnames

`KC_HOSTNAME=http://localhost:8080` makes every token carry `iss=http://localhost:8080/realms/northwind`,
whichever address it was minted through. Containers reach Keycloak as `http://keycloak:8080`
(`KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`), so the API fetches signing keys from the internal URL but
checks `iss` against the public one.

## Reset

Keycloak imports `realm-northwind.json` only when the realm does not exist yet. Compose runs it in
dev mode with an embedded database that lives inside the container, so removing the container
resets everything: `docker compose rm -sf keycloak && docker compose up -d keycloak`. Do this after
editing the JSON or rotating `RAG_WEB_CLIENT_SECRET`.

## Real deployments: group names

The demo realm emits bare group names (`full.path=false`), which is fine for two flat groups.
In a real realm a sub-group `/Parent/HR` would be flattened into `HR` and collide with the top-level
`HR` ACL. Use a full-path mapper and set `OIDC_GROUPS_REQUIRE_LEADING_SLASH=true`: the API then
rejects bare names and nested paths instead of guessing.

## Production

Use `start` (not `start-dev`) behind TLS with a real database, secrets from a secret store, exact
production redirect URIs, and your own realm. Do not use this export.
