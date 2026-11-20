"""Conversation persistence models shared by the Skuld broker surfaces."""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

CONVERSATION_HISTORY_DIR = ".skuld"
CONVERSATION_HISTORY_FILE = "conversation.json"


@dataclass
class ConversationTurn:
    """A single turn in the conversation history."""

    id: str
    role: str  # "user" | "assistant"
    content: str
    parts: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict = field(default_factory=dict)
    participant_id: str | None = None
    participant_meta: dict | None = None
    thread_id: str | None = None
    visibility: str = "public"
    # Who said a human turn: {"user_id", "display_name"}, stamped from the verified
    # connection (see niuu.domain.message_author). None for assistant turns, for
    # senders that are not a verified person, and for turns recorded before
    # authorship existed.
    author: dict | None = None
    # Who sent a user-role turn (LEX-1910, ``niuu.domain.message_sender``), set by the
    # server from the message's path. ``None`` on the model's own turns (the role says
    # it) and on user turns recorded before senders were labelled.
    sender: dict | None = None


CHRONICLE_SUMMARY_PROMPT = """\
Summarize this coding session in JSON format. Be concise.
Respond ONLY with the JSON object, no markdown fencing, no commentary.

{
  "summary": "One paragraph describing what was accomplished in this session.",
  "key_changes": ["file_or_component: brief description of change", ...],
  "unfinished_work": "Description of anything left incomplete, or null if done."
}
"""

SUMMARY_TIMEOUT_SECONDS = 15


def turn_record(turn: ConversationTurn) -> dict:
    """A turn as stored and sent: an unlabelled turn has no ``sender`` key (LEX-1910),
    the same as its rebuild from the durable log."""
    record = asdict(turn)
    if record["sender"] is None:
        del record["sender"]
    return record
