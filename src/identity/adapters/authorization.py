"""Explicitly disabled resource authorization for trusted development deployments."""

from identity.models import Principal, Resource
from identity.ports import AuthorizationPort


class AllowAllAuthorizationAdapter(AuthorizationPort):
    """Development adapter that permits all actions.

    For local dev only — no authorization checks.
    """

    def __init__(self, **_extra: object) -> None:
        pass

    async def is_allowed(
        self,
        principal: Principal,
        action: str,
        resource: Resource,
    ) -> bool:
        return True

    async def filter_allowed(
        self,
        principal: Principal,
        action: str,
        resources: list[Resource],
    ) -> list[Resource]:
        return resources
