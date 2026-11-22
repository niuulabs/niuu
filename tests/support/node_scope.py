"""What the web UI sends to one Forge node, shared with the web adapter's own test."""

from __future__ import annotations

import json
from pathlib import Path

from niuu.domain.guild_membership import GuildInstance, GuildMembership

NODE_REQUESTS = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "forge_node_requests.json").read_text()
)
NODE_MEMBERSHIP = GuildMembership(
    guild_url=NODE_REQUESTS["guild_url"],
    instance=GuildInstance(**NODE_REQUESTS["instance"]),
)
OTHER_INSTANCE = "ae40cfda-valhalla"
OTHER_INSTANCE_DETAIL = (
    f"Instance {OTHER_INSTANCE} is not this Forge node (Noatun, 70b502d8-noatun); "
    "reach other Forges through the Guild at https://guild.example"
)
#: Every way a launch may name tags; a node refuses each, for sessions and teams alike.
TAG_SELECTORS = [
    {"target_tags": ["gpu"], "target_match": "any"},
    {"target_tags": ["gpu"]},
    {"target_match": "all"},
    {"targetTags": ["gpu"], "targetMatch": "all"},
    {"target_tags": ["gpu"], "instance_id": "70b502d8-noatun"},
]
#: Naming no tags selects nothing, as at the Guild facade.
NO_TAG_SELECTORS = [{"target_tags": []}, {"target_tags": None, "target_match": None}]
