"""Who said a human message in a session.

A session can have more than one person speaking in it. Every human turn the
Skuld broker records carries the verified identity the session proxy stamped on
the connection that sent it, so the transcript, the replayed history, the trace
and the model can all say who spoke. Turns recorded before authorship existed
carry none, and that is how they stay: "no author" is the true state of old
data, never something to fill in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

AUTHOR_KEY = "author"


@dataclass(frozen=True)
class MessageAuthor:
    """A verified person: the identity provider's user id and the name to show.

    ``display_name`` is what the session proxy stamped (the provisioned user's
    display name, else their email). It is empty only when the proxy had
    neither; ``label`` then names the person by id, which is an identifier the
    caller really has rather than a name made up for them.
    """

    user_id: str
    display_name: str = ""

    @property
    def label(self) -> str:
        """The name the model and the trace show for this person."""
        return self.display_name or self.user_id

    def to_dict(self) -> dict[str, str]:
        """The wire and storage shape: ``{"user_id": ..., "display_name": ...}``."""
        return {"user_id": self.user_id, "display_name": self.display_name}

    @classmethod
    def from_wire(cls, raw: Any) -> MessageAuthor | None:
        """Read an ``author`` object off a frame or stored turn.

        Absent or malformed means the message has no recorded author (old data
        and non-human senders); it is not an error.
        """
        if not isinstance(raw, dict):
            return None
        user_id = raw.get("user_id")
        if not isinstance(user_id, str) or not user_id.strip():
            return None
        display_name = raw.get("display_name")
        return cls(
            user_id=user_id.strip(),
            display_name=display_name.strip() if isinstance(display_name, str) else "",
        )
