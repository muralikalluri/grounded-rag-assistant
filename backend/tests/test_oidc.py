# ruff: noqa: E501  (dense token/claim tables)
import base64
import json
import logging
from typing import Any

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_chat_service
from app.auth.oidc import AuthError, JwksUnavailable, OidcVerifier, normalise_groups
from app.config import Settings, get_settings
from app.generation.answer import ChatService
from app.main import app
from tests.support.chunks import make_chunk
from tests.support.llm import FakeProvider, StubRetrieval, answer_json
from tests.support.oidc import ISSUER, JWKS_URL, FakeJwks, Keys, mint


def cfg(**kw: Any) -> Settings:
    return Settings(oidc_issuer=ISSUER, oidc_jwks_url=JWKS_URL, **kw)


@pytest.fixture
def keys() -> Keys:
    return Keys()


@pytest.fixture
def jwks(keys: Keys) -> FakeJwks:
    return FakeJwks(keys)


def verifier(jwks: FakeJwks, **kw: Any) -> OidcVerifier:
    return OidcVerifier(cfg(**kw), transport=jwks.transport)


async def rejected(v: OidcVerifier, token: str) -> str:
    with pytest.raises(AuthError) as exc:
        await v.verify(token)
    return exc.value.reason


# ------------------------------------------------------------------------ happy path
async def test_valid_token_becomes_a_principal_with_normalised_groups(
    keys: Keys, jwks: FakeJwks
) -> None:
    p = await verifier(jwks).verify(mint(keys, groups=["/HR"]))
    assert p.user_id == "alice" and p.groups == {"HR"} and p.roles == {"user"}


async def test_admin_role_comes_only_from_realm_roles_and_grants_no_groups(
    keys: Keys, jwks: FakeJwks
) -> None:
    v = verifier(jwks)
    admin = await v.verify(
        mint(keys, groups=[], realm_access={"roles": ["admin"]}, preferred_username="admin")
    )
    assert "admin" in admin.roles and admin.groups == frozenset()  # role != read access
    spoof = await v.verify(mint(keys, resource_access={"rag-web": {"roles": ["admin"]}}))
    assert "admin" not in spoof.roles  # client roles are ignored


async def test_missing_preferred_username_falls_back_to_sub(keys: Keys, jwks: FakeJwks) -> None:
    p = await verifier(jwks).verify(mint(keys, preferred_username=None))
    assert p.user_id == "11111111-2222-3333-4444-555555555555"


# --------------------------------------------------------------------- token rejections
@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"exp": 1}, "expired"),
        ({"iss": "http://evil.example/realms/x"}, "bad_issuer"),
        ({"aud": ["account"]}, "bad_audience"),  # Keycloak's default audience alone is not enough
        ({"azp": "some-other-client"}, "bad_azp"),
        ({"typ": "ID"}, "bad_typ"),  # an ID token
        ({"typ": "Refresh"}, "bad_typ"),
        ({"typ": None}, "bad_typ"),
        ({"groups": "HR"}, "groups_claim_missing_or_invalid"),
        ({"groups": ["/a/b"]}, "group_path_not_supported"),
        ({"groups": [""]}, "groups_claim_invalid_entry"),
        ({"groups": [5]}, "groups_claim_invalid_entry"),
        ({"groups": ["HR "]}, "group_path_not_supported"),
        ({"sub": None}, "invalid_token"),
        ({"iat": None}, "invalid_token"),
        ({"exp": None}, "invalid_token"),
    ],
)
async def test_bad_claims_are_rejected(
    keys: Keys, jwks: FakeJwks, over: dict[str, Any], reason: str
) -> None:
    assert await rejected(verifier(jwks), mint(keys, **over)) == reason


async def test_not_yet_valid_and_future_iat_are_rejected(keys: Keys, jwks: FakeJwks) -> None:
    import time

    future = int(time.time()) + 3600
    assert await rejected(verifier(jwks), mint(keys, nbf=future)) == "invalid_token"
    assert (
        await rejected(verifier(jwks), mint(keys, iat=future, exp=future + 300)) == "invalid_token"
    )


async def test_small_clock_skew_is_tolerated_but_only_within_the_leeway(
    keys: Keys, jwks: FakeJwks
) -> None:
    import time

    just_expired = mint(keys, exp=int(time.time()) - 5)
    assert (await verifier(jwks).verify(just_expired)).user_id == "alice"
    assert await rejected(verifier(jwks, oidc_leeway_seconds=0.0), just_expired) == "expired"


async def test_absent_groups_claim_means_no_memberships_not_an_error(
    keys: Keys, jwks: FakeJwks
) -> None:
    """Keycloak omits `groups` entirely for users without groups (the admin): valid, sees nothing."""
    p = await verifier(jwks).verify(mint(keys, groups=None, realm_access={"roles": ["admin"]}))
    assert p.groups == frozenset() and "admin" in p.roles
    # ... but a present-and-malformed claim still fails closed
    assert (
        await rejected(verifier(jwks), mint(keys, groups={"HR": True}))
        == "groups_claim_missing_or_invalid"
    )


async def test_group_names_are_never_case_folded(keys: Keys, jwks: FakeJwks) -> None:
    p = await verifier(jwks).verify(mint(keys, groups=["hr", "HR"]))
    assert p.groups == {"hr", "HR"}


def test_normalise_groups_direct() -> None:
    assert normalise_groups(["/HR", "Engineering"], 10) == {"HR", "Engineering"}
    assert normalise_groups([], 10) == frozenset()  # valid principal that matches nothing
    with pytest.raises(AuthError):
        normalise_groups(["a"] * 11, 10)


# ---------------------------------------------------------------- algorithm attacks
async def test_alg_none_is_rejected(keys: Keys, jwks: FakeJwks) -> None:
    def b64(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    forged = f"{b64({'alg': 'none', 'kid': keys.kid})}.{b64({'iss': ISSUER, 'sub': 'x'})}."
    assert await rejected(verifier(jwks), forged) == "bad_alg"


async def test_hs256_signed_with_the_public_key_as_secret_is_rejected(
    keys: Keys, jwks: FakeJwks
) -> None:
    """Classic algorithm-confusion attack: HMAC using the (public) RSA key bytes as the secret."""
    import hashlib
    import hmac
    import time

    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    now = int(time.time())
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": keys.kid}).encode())
    payload = b64(json.dumps({"iss": ISSUER, "aud": "rag-api", "azp": "rag-web", "typ": "Bearer",
                              "sub": "x", "iat": now, "exp": now + 300, "groups": ["HR"]}).encode())  # fmt: skip
    sig = b64(hmac.new(keys.pem_public(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    assert await rejected(verifier(jwks), f"{header}.{payload}.{sig}") == "bad_alg"


async def test_tampered_payload_and_foreign_signature_are_rejected(
    keys: Keys, jwks: FakeJwks
) -> None:
    token = mint(keys)
    head, payload, sig = token.split(".")
    evil = base64.urlsafe_b64encode(json.dumps({**jwt.decode(token, options={"verify_signature": False}),
                                                 "groups": ["HR", "Engineering"]}).encode()).rstrip(b"=").decode()  # fmt: skip
    assert await rejected(verifier(jwks), f"{head}.{evil}.{sig}") == "invalid_token"
    other = Keys(kid=keys.kid)  # same kid, different key pair
    assert await rejected(verifier(jwks), mint(other)) == "invalid_token"


async def test_garbage_and_oversize_tokens_are_rejected_cheaply(keys: Keys, jwks: FakeJwks) -> None:
    v = verifier(jwks)
    assert await rejected(v, "not-a-jwt") == "malformed"
    assert await rejected(v, "a" * 9000) == "token_too_large"
    assert jwks.calls == 0  # rejected before any key fetch


# ------------------------------------------------------------------------ JWKS handling
async def test_unknown_kid_refetches_once_then_not_again_within_the_window(
    keys: Keys, jwks: FakeJwks
) -> None:
    v = verifier(jwks)
    await v.verify(mint(keys))  # loads the cache (1 fetch)
    stranger = Keys(kid="rotated-key")
    assert await rejected(v, mint(stranger)) == "unknown_kid"
    assert jwks.calls == 2  # exactly one forced refetch
    assert await rejected(v, mint(stranger)) == "unknown_kid"
    assert jwks.calls == 2  # not again inside the 30 s window (no JWKS hammering)


async def test_key_rotation_is_picked_up_via_the_refetch(keys: Keys, jwks: FakeJwks) -> None:
    v = verifier(jwks)
    await v.verify(mint(keys))
    new = Keys(kid="rotated-key")
    jwks.keys = [keys, new]
    assert (await v.verify(mint(new))).user_id == "alice"


async def test_cached_keys_keep_working_when_the_jwks_endpoint_dies(
    keys: Keys, jwks: FakeJwks
) -> None:
    v = verifier(jwks, oidc_jwks_ttl_seconds=0.0)  # every call wants a refresh
    await v.verify(mint(keys))
    jwks.fail = True
    assert (await v.verify(mint(keys))).user_id == "alice"


async def test_no_cache_and_jwks_down_is_a_server_error_not_an_auth_failure(
    keys: Keys, jwks: FakeJwks
) -> None:
    jwks.fail = True
    with pytest.raises(JwksUnavailable):
        await verifier(jwks).verify(mint(keys))


async def test_tokens_never_reach_the_logs(
    keys: Keys, jwks: FakeJwks, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    token = mint(keys, groups="not-a-list")
    with pytest.raises(AuthError):
        await verifier(jwks).verify(token)
    from app.logging import redact

    assert token.split(".")[1] not in "\n".join(
        f"{r.getMessage()} {r.__dict__}" for r in caplog.records
    )
    assert "[redacted-jwt]" in redact(f"oops {token}") and token not in redact(f"oops {token}")


# ------------------------------------------------------------------------- config rules
def test_config_rules_for_oidc() -> None:
    with pytest.raises(ValueError, match="dev_auth_enabled"):
        Settings(oidc_issuer=ISSUER, oidc_jwks_url=JWKS_URL, dev_auth_enabled=True)
    with pytest.raises(ValueError, match="oidc_jwks_url"):
        Settings(oidc_issuer=ISSUER)
    with pytest.raises(ValueError, match="requires oidc_issuer"):
        Settings(app_env="prod", audit_hmac_key="a-real-secret-value")
    Settings(
        app_env="prod",
        audit_hmac_key="a-real-secret-value",
        oidc_issuer=ISSUER,
        oidc_jwks_url=JWKS_URL,
    )


# --------------------------------------------------------------------------- HTTP layer
LEAVE = "Employees may carry forward a maximum of 10 days of unused annual leave."
GOOD = ("You can carry forward up to 10 days.", [("S1", "carry forward a maximum of 10 days")])


@pytest.fixture
async def client(keys: Keys, jwks: FakeJwks):  # type: ignore[no-untyped-def]
    settings = cfg(app_env="test")
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.oidc = OidcVerifier(settings, transport=jwks.transport)
    svc = ChatService(
        StubRetrieval([make_chunk(LEAVE)]), FakeProvider([answer_json([GOOD])] * 5), settings
    )  # type: ignore[arg-type]
    app.dependency_overrides[get_chat_service] = lambda: svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    app.dependency_overrides.clear()
    app.state.oidc = None


BODY = {"question": "How many days can I carry forward?"}


async def test_api_accepts_a_bearer_token_and_ignores_the_dev_header(
    client: AsyncClient, keys: Keys
) -> None:
    ok = await client.post(
        "/api/chat", json=BODY, headers={"Authorization": f"Bearer {mint(keys)}"}
    )
    assert ok.status_code == 200 and '"status": "answered"' in ok.text
    # the dev header alone (even a valid dev user) authenticates nobody when OIDC is on
    dev = await client.post("/api/chat", json=BODY, headers={"X-Dev-User": "alice"})
    assert dev.status_code == 401


async def test_every_auth_failure_looks_identical_to_the_client(
    client: AsyncClient, keys: Keys
) -> None:
    bad = [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer garbage"},
        {"Authorization": f"Bearer {mint(keys, exp=1)}"},
        {"Authorization": f"Bearer {mint(keys, aud=['account'])}"},
        {"Authorization": f"Bearer {mint(keys, groups='HR')}"},
    ]
    responses = [await client.post("/api/chat", json=BODY, headers=h) for h in bad]
    assert {r.status_code for r in responses} == {401}
    assert len({r.text for r in responses}) == 1  # no oracle for why it failed
    assert all(r.headers["www-authenticate"] == "Bearer" for r in responses)


async def test_jwks_outage_with_no_cache_is_503(
    client: AsyncClient, keys: Keys, jwks: FakeJwks
) -> None:
    jwks.fail = True
    r = await client.post("/api/chat", json=BODY, headers={"Authorization": f"Bearer {mint(keys)}"})
    assert r.status_code == 503


async def test_a_token_with_empty_groups_yields_a_principal_that_matches_nothing(
    client: AsyncClient, keys: Keys, jwks: FakeJwks
) -> None:
    """Authentication succeeds (200) and the principal carries no groups. That no data is returned
    for empty groups is proven against the real SQL in test_retrieval_acl.py."""
    from app.api.deps import get_principal

    r = await client.post(
        "/api/chat", json=BODY, headers={"Authorization": f"Bearer {mint(keys, groups=[])}"}
    )
    assert r.status_code == 200
    p = await OidcVerifier(cfg(app_env="test"), transport=jwks.transport).verify(
        mint(keys, groups=[])
    )
    assert p.groups == frozenset() and get_principal is not None


async def test_jwks_outage_backs_off_instead_of_stalling_every_request(
    keys: Keys, jwks: FakeJwks
) -> None:
    v = verifier(jwks, oidc_jwks_ttl_seconds=0.0)  # cache is always "stale"
    await v.verify(mint(keys))
    jwks.fail = True
    before = jwks.calls
    for _ in range(20):
        assert (await v.verify(mint(keys))).user_id == "alice"  # served from the cache
    assert jwks.calls - before <= 1  # one failed attempt, then backoff; not one per request


async def test_full_path_groups_can_be_required_for_real_realms(keys: Keys, jwks: FakeJwks) -> None:
    strict = verifier(jwks, oidc_groups_require_leading_slash=True)
    assert (await strict.verify(mint(keys, groups=["/HR"]))).groups == {"HR"}
    # a sub-group flattened by a full.path=false mapper arrives bare and is refused
    assert await rejected(strict, mint(keys, groups=["HR"])) == "group_path_not_full"
    # ... while nested full paths are still not guessed at
    assert await rejected(strict, mint(keys, groups=["/Parent/HR"])) == "group_path_not_supported"


async def test_require_admin_guards_by_role_and_grants_no_data_access() -> None:
    from fastapi import Depends, FastAPI

    from app.api.deps import get_principal, require_admin
    from app.auth.principal import Principal

    tiny = FastAPI()

    @tiny.get("/admin-only")
    async def admin_only(p: Principal = Depends(require_admin)) -> dict[str, list[str]]:  # noqa: B008
        return {"groups": sorted(p.groups)}

    async def as_admin() -> Principal:
        return Principal("root", frozenset(), frozenset({"admin", "user"}))

    async def as_user() -> Principal:
        return Principal("alice", frozenset({"HR"}), frozenset({"user"}))

    async with AsyncClient(transport=ASGITransport(app=tiny), base_url="http://t") as c:
        tiny.dependency_overrides[get_principal] = as_user
        assert (await c.get("/admin-only")).status_code == 403
        tiny.dependency_overrides[get_principal] = as_admin
        r = await c.get("/admin-only")
        assert r.status_code == 200 and r.json() == {"groups": []}  # the role carries no groups
