"""Locally minted Keycloak-shaped tokens + a fake JWKS endpoint (no network, no Keycloak)."""

import json
import time
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "http://localhost:8080/realms/northwind"
JWKS_URL = "http://keycloak.internal:8080/realms/northwind/protocol/openid-connect/certs"


class Keys:
    def __init__(self, kid: str = "key-1") -> None:
        self.kid = kid
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.public = self.private.public_key()

    def jwk(self) -> dict[str, Any]:
        d = json.loads(RSAAlgorithm.to_jwk(self.public))
        return {**d, "kid": self.kid, "use": "sig", "alg": "RS256"}

    def pem_public(self) -> bytes:
        return self.public.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )


def mint(keys: Keys, **over: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER, "aud": ["rag-api", "account"], "azp": "rag-web", "typ": "Bearer",
        "sub": "11111111-2222-3333-4444-555555555555", "preferred_username": "alice",
        "iat": now, "exp": now + 300, "groups": ["HR"], "realm_access": {"roles": ["user"]},
    }  # fmt: skip
    claims.update(over)
    for k in [k for k, v in claims.items() if v is None]:
        del claims[k]  # pass name=None to drop a claim
    return jwt.encode(claims, keys.private, algorithm="RS256", headers={"kid": keys.kid})


class FakeJwks:
    """httpx transport serving a mutable JWKS; counts fetches."""

    def __init__(self, *keys: Keys) -> None:
        self.keys = list(keys)
        self.calls = 0
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.fail:
            return httpx.Response(503)
        assert str(request.url) == JWKS_URL
        return httpx.Response(200, json={"keys": [k.jwk() for k in self.keys]})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
