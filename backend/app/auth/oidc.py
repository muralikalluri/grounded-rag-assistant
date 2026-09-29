"""OIDC access-token verification (Keycloak, RS256).

Hardening, each backed by a test:
- the algorithm is pinned to RS256 (never read from the token header): ``none`` and HS256 tokens
  signed with the public key as an HMAC secret (alg confusion) are rejected
- ``iss`` must equal the configured public issuer, ``aud`` must contain the API audience,
  ``azp`` must be our web client, ``typ`` must be ``Bearer`` (rejects ID / refresh tokens)
- ``exp``, ``iat``, ``sub`` are required; small configured leeway
- the ``groups`` claim, if present, must be a list; Keycloak paths ("/HR") are normalised to the
  exact names used by collection ACLs; nested paths are rejected; nothing is case-folded.
  Keycloak OMITS the claim for users with no groups (e.g. the admin), so an absent claim means
  "no memberships": a valid principal that matches nothing (least privilege), never an error
- signing keys come from a cached JWKS: an unknown ``kid`` triggers at most one refetch per
  ``oidc_jwks_min_refetch_seconds``; if the JWKS is unreachable, cached keys keep working and,
  with no cache, the API answers 503 (not 401)
Tokens are never logged.
"""

import asyncio
import json
import logging
import time
from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from app.auth.principal import Principal
from app.config import Settings

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Authentication failed. ``reason`` is a short code for logs; it is never sent to clients."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class JwksUnavailable(Exception):
    """No signing keys and the JWKS endpoint cannot be reached (server-side problem: 503)."""


def normalise_groups(
    raw: Any, max_groups: int, require_leading_slash: bool = False
) -> frozenset[str]:
    """Keycloak group paths -> exact ACL group names. Fail closed on anything odd.

    With ``require_leading_slash`` (full-path mappers, the safe setting for real realms) every
    entry must be a full path ("/HR"); a bare name is rejected. This matters because
    ``full.path=false`` would flatten a sub-group "/Parent/HR" into "HR".
    """
    if not isinstance(raw, list):
        raise AuthError("groups_claim_missing_or_invalid")
    if len(raw) > max_groups:
        raise AuthError("too_many_groups")
    out: set[str] = set()
    for g in raw:
        if not isinstance(g, str) or not g:
            raise AuthError("groups_claim_invalid_entry")
        if require_leading_slash and not g.startswith("/"):
            raise AuthError("group_path_not_full")
        name = g[1:] if g.startswith("/") else g
        if not name or "/" in name or name != name.strip():
            raise AuthError("group_path_not_supported")  # nested paths are not guessed at
        out.add(name)
    return frozenset(out)


class OidcVerifier:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not settings.oidc_issuer or not settings.oidc_jwks_url:
            raise ValueError("oidc_issuer and oidc_jwks_url are required")
        self._s = settings
        self._issuer = settings.oidc_issuer
        self._jwks_url = settings.oidc_jwks_url
        self._http = httpx.AsyncClient(
            transport=transport, timeout=settings.oidc_jwks_timeout_seconds, trust_env=False
        )
        self._last_failed = float("-inf")
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0
        self._last_forced = float("-inf")
        self._lock = asyncio.Lock()
        self.fetch_count = 0  # observable in tests

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------- key management
    async def _fetch(self) -> None:
        resp = await self._http.get(self._jwks_url)
        resp.raise_for_status()
        keys: dict[str, Any] = {}
        for jwk in resp.json().get("keys", []):
            if jwk.get("kty") == "RSA" and jwk.get("use", "sig") == "sig" and jwk.get("kid"):
                keys[jwk["kid"]] = RSAAlgorithm.from_jwk(json.dumps(jwk))
        if not keys:
            raise ValueError("JWKS has no usable RSA signing keys")
        self._keys, self._fetched_at = keys, time.monotonic()
        self.fetch_count += 1

    async def _key_for(self, kid: str) -> Any:
        async with self._lock:
            now = time.monotonic()
            stale = now - self._fetched_at > self._s.oidc_jwks_ttl_seconds
            backing_off = now - self._last_failed < self._s.oidc_jwks_min_refetch_seconds
            if (not self._keys or stale) and not backing_off:
                try:
                    await self._fetch()
                except Exception:
                    # Back off: otherwise every request would retry (and wait for) a dead
                    # endpoint, holding this lock, while cached keys could serve them instantly.
                    self._last_failed = now
                    logger.warning("jwks fetch failed", extra={"cached_keys": len(self._keys)})
            if not self._keys:
                raise JwksUnavailable
            if kid not in self._keys:
                if now - self._last_forced >= self._s.oidc_jwks_min_refetch_seconds:
                    self._last_forced = now  # single refetch per window, hit or miss
                    try:
                        await self._fetch()
                    except Exception:
                        logger.warning("jwks refetch failed")
            key = self._keys.get(kid)
            if key is None:
                raise AuthError("unknown_kid")
            return key

    # --------------------------------------------------------------------- verification
    async def verify(self, token: str) -> Principal:
        s = self._s
        if len(token.encode()) > s.oidc_max_token_bytes:
            raise AuthError("token_too_large")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AuthError("malformed") from None
        if header.get("alg") != "RS256":
            raise AuthError("bad_alg")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthError("missing_kid")
        key = await self._key_for(kid)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=s.oidc_audience,
                issuer=self._issuer,
                leeway=s.oidc_leeway_seconds,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError:
            raise AuthError("expired") from None
        except jwt.InvalidAudienceError:
            raise AuthError("bad_audience") from None
        except jwt.InvalidIssuerError:
            raise AuthError("bad_issuer") from None
        except jwt.PyJWTError:
            raise AuthError("invalid_token") from None

        if claims.get("azp") != s.oidc_client_id:
            raise AuthError("bad_azp")
        if claims.get("typ") != "Bearer":
            raise AuthError("bad_typ")  # ID and refresh tokens are not access tokens
        raw_groups = claims.get(s.oidc_groups_claim)
        groups = (
            frozenset()
            if raw_groups is None
            else normalise_groups(
                raw_groups, s.oidc_max_groups, s.oidc_groups_require_leading_slash
            )
        )
        roles_claim = (claims.get("realm_access") or {}).get("roles", [])
        is_admin = isinstance(roles_claim, list) and s.oidc_admin_role in roles_claim
        username = claims.get("preferred_username")
        user_id = username if isinstance(username, str) and username else str(claims["sub"])
        return Principal(user_id, groups, frozenset({"admin", "user"} if is_admin else {"user"}))
