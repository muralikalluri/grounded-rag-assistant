import logging
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.auth.oidc import AuthError, JwksUnavailable, OidcVerifier
from app.auth.principal import Principal, dev_principal
from app.config import Settings, get_settings
from app.generation.answer import ChatService

logger = logging.getLogger(__name__)

_UNAUTHENTICATED = HTTPException(
    status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"}
)


async def get_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
    x_dev_user: Annotated[str | None, Header()] = None,
) -> Principal:
    """Who is calling. OIDC bearer token when configured; otherwise the dev header (dev/test only).

    When OIDC is configured the dev header is ignored entirely. Every failure is the same 401 so
    clients learn nothing about why; the reason code goes to the log (never the token).
    """
    if settings.oidc_issuer:
        verifier: OidcVerifier | None = getattr(request.app.state, "oidc", None)
        if verifier is None:
            raise HTTPException(status_code=503, detail="Authentication is not ready")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _UNAUTHENTICATED
        try:
            return await verifier.verify(token.strip())
        except AuthError as exc:
            logger.info("auth rejected", extra={"reason": exc.reason})
            raise _UNAUTHENTICATED from None
        except JwksUnavailable:
            raise HTTPException(status_code=503, detail="Authentication is unavailable") from None
    if (
        settings.dev_auth_enabled
        and settings.app_env in ("dev", "test")
        and x_dev_user is not None
        and x_dev_user in settings.dev_principals
    ):
        return dev_principal(x_dev_user, settings)
    raise _UNAUTHENTICATED


async def require_admin(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    """Guard for admin-only endpoints. The role grants NO read bypass (groups decide access).

    No admin endpoint exists yet (the admin UI is Later; crawls are an operator CLI). It is kept,
    and tested, so the first one is built on a checked dependency rather than an ad-hoc test.
    """
    if "admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="Forbidden")
    return principal


def get_chat_service(request: Request) -> ChatService:
    service: ChatService | None = getattr(request.app.state, "chat_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Chat service is not ready")
    return service
