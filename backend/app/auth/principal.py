"""The authenticated caller. Every retrieval path takes one; there is no "no principal" mode."""

from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True)
class Principal:
    user_id: str
    groups: frozenset[str]
    roles: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        # Group names are matched exactly against collection ACLs: no case folding, no trimming.
        # Callers (the OIDC layer) must normalise IdP paths like "/HR" -> "HR" before this.
        for g in self.groups:
            if not g or g != g.strip():
                raise ValueError("group names must be non-empty and not padded with whitespace")


def dev_principal(user_id: str, settings: Settings) -> Principal:
    """Demo users for dev/test (alice: HR, bob: Engineering). Real requests get OIDC claims."""
    if user_id not in settings.dev_principals:
        raise KeyError(f"unknown dev user: {user_id}")
    roles = {"admin"} if user_id in settings.dev_admins else {"user"}
    return Principal(user_id, frozenset(settings.dev_principals[user_id]), frozenset(roles))
