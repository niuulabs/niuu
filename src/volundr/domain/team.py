"""How a Forge team is recorded on its sessions' stored ``workload_config``.

A team is a unit (a root created with ``hosts_members`` and member sessions ``hosted_by`` it)
whose root hosts a room of the team's own mesh realm and runs no agent of its own. Every agent
of the team is a member session with its own persona and engine; exactly one of them leads.
People join the room through ordinary session participant grants on the root.

The root carries ``TEAM_KEY``; each member carries ``TEAM_MEMBER_KEY``. The contributors that
build the runtime config, and the session and members responses, read these keys and nothing
else to tell a team from any other unit.
"""

from typing import Literal

#: On the team's root: ``{"task_to": "lead" | "everyone" | None}``. ``None`` means the team
#: was started without a task and waits for the first message in its room.
TEAM_KEY = "team"

#: On each member of a team: ``{"lead": bool}``.
TEAM_MEMBER_KEY = "team_member"

#: Who receives the opening task, when one is given at start.
TeamTaskTarget = Literal["lead", "everyone"]
TEAM_TASK_TARGETS: tuple[str, ...] = ("lead", "everyone")


def team_realm(root_session_id: str) -> str:
    """The mesh realm of one team: its members, and only its members."""
    return f"team-{root_session_id}"
