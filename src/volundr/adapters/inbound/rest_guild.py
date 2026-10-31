"""``GET /api/v1/forge/guild``: the Guild this Forge node belongs to.

A node's own notification feed is node-only. A client that registered the node
directly reads this to find the Guild whose facade merges every member's feed,
and the instance id the Guild keys this node's items and read state by.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from niuu.domain.guild_membership import GuildMembership
from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.models import Principal

NOT_JOINED_DETAIL = "This Forge node is not joined to a Guild"
INSTANCE_UNKNOWN_DETAIL = (
    "This Forge node belongs to the Guild at {guild_url}, but its instance id there is not "
    "recorded. On a host joined with `niuu join`, run `niuu guild heartbeat --once`; on a "
    "cluster, set the Volundr chart's guild.instanceId, guild.instanceName and "
    "guild.instanceSlug from the Guild's registry (GET /api/v1/niuu/targets/volundr)."
)


class GuildMembershipResponse(BaseModel):
    guild_url: str
    instance_id: str
    instance_name: str
    instance_slug: str


def create_guild_router(
    membership: GuildMembership | None,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    """Serve this node's Guild membership.

    404 when the node belongs to no Guild; 503 when it knows its Guild but not its
    own instance there yet (the rest of Forge keeps running, with null instance
    fields on its items).
    """
    router = APIRouter(prefix=prefix, tags=["Guild"])

    @router.get(
        "/guild",
        response_model=GuildMembershipResponse,
        responses={
            404: {"description": NOT_JOINED_DETAIL},
            503: {"description": "The node's instance in its Guild is not recorded yet"},
        },
    )
    async def get_guild(
        _principal: Principal = Depends(extract_principal),
    ) -> GuildMembershipResponse:
        if membership is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_JOINED_DETAIL)
        if membership.instance is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                INSTANCE_UNKNOWN_DETAIL.format(guild_url=membership.guild_url),
            )
        return GuildMembershipResponse(**membership.wire())

    return router
