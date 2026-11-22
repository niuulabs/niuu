"""Where a launch asks to run: the selectors the Guild facade resolves.

The facade picks a Forge from ``instance_id`` or ``target_tags``/``target_match`` and
drops them before it forwards the launch, so a Forge sees them only from a client that
talks to it directly. It takes its own instance id and refuses everything else: a
selector is never ignored, because ignoring it starts the launch somewhere the caller
did not ask for.

A Forge cannot resolve tags itself. The Guild matches a selector against the Forge's own
tags first and asks the Forge's machines only for the rest
(``niuu.domain.tags.residual_selector``), and a Forge's own tags exist only in the Guild's
instance registry. Asking the machines for the whole selector would mean something else.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from pydantic import AliasChoices, BaseModel, Field

from niuu.domain.guild_membership import GuildMembership
from volundr.adapters.inbound.rest_guild import require_own_instance
from volundr.domain.session_runtimes import HOST_SELECTOR_KEY

TAG_SELECTOR_DETAIL = (
    "This Forge node cannot place a launch by target_tags/target_match: a tag selector is "
    "matched first against the Forge's own tags, which only its Guild knows. {remedy} drop "
    "them and name the tags the machine must carry in workload_config."
    f"{HOST_SELECTOR_KEY}."
)


class LaunchTarget(BaseModel):
    """The placement selectors of a launch request, as the Guild facade takes them."""

    instance_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("instance_id", "instanceId"),
        description=(
            "Guild instance id of the Forge to launch on, as the Guild facade takes it; must "
            "be this node's own (another Forge's id is a 404)"
        ),
    )
    target_tags: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("target_tags", "targetTags"),
        description=(
            "Tags the Guild facade picks a Forge and a machine by. Refused by a node, which "
            "does not know its own registry tags"
        ),
    )
    target_match: str | None = Field(
        default=None,
        validation_alias=AliasChoices("target_match", "targetMatch"),
        description="How the facade matches target_tags: all or any. Refused like them",
    )


def require_own_target(membership: GuildMembership | None, target: LaunchTarget) -> None:
    """Refuse a launch this node would run anywhere but where the caller asked.

    Raises:
        HTTPException: as ``require_own_instance`` for another Forge's id; 422 for a tag
            selector, with where to send it instead.
    """
    require_own_instance(membership, target.instance_id)
    if not target.target_tags and not target.target_match:
        return
    remedy = (
        f"Send it to the Guild at {membership.guild_url}, or"
        if membership is not None
        else "This node belongs to no Guild:"
    )
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT, TAG_SELECTOR_DETAIL.format(remedy=remedy)
    )
