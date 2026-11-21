"""Durable turn count and settled tail — folded incrementally from the session event log.

The conversation read path compares a running session's live broker body with its durable
log to catch a broker that came back BEHIND the log (FAULT C). That check needs only two
facts about the durable side: how many turns it holds and the id of its last settled turn.

It used to get them from the full durable rebuild (up to 50k frames decoded into turns)
on every poll whose log head had moved. A running session's head moves all the time, so a
Forge serving a few long sessions rebuilt them continuously and in parallel, and was
OOMKilled within a minute of every start (forge-hosts, 2026-11-21).

``TurnTailFold`` gets the same two facts the way ``AttachmentsFold`` folds attachments:
the log is settled up to its newest assistant ``conversation.turn`` (the rebuild never
reduces raw frames before one), only the count and the last id of settled turns are kept,
and a refresh reads just the frames after the last boundary. A poll between turns
therefore reads the few frames since the last turn ended, not the session.

Pure: no I/O, no ``skuld`` imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from niuu.domain.transcript_rebuild import rebuild_turns
from volundr.domain.services.attachments_index import settle_pending

if TYPE_CHECKING:
    from volundr.domain.models import SessionLogEntry


def last_settled_turn_id(turns: object) -> str | None:
    """Id of the last turn that is NOT an in-progress row (the comparable transcript tail).

    Used by the FAULT-C tail-id gate: a broker that is genuinely BEHIND the durable log
    (resumed from a stale snapshot) is missing trailing turns, so its settled tail id differs
    from the rebuild's. A broker whose body merely SEGMENTS the same content differently
    (e.g. `present_file` deliveries folded vs standalone) still ends on the same settled turn.
    Interrupted-flush rows count as real tail (a crashed turn the live body lost IS a desync).
    """
    if not isinstance(turns, list):
        return None
    for t in reversed(turns):
        if not isinstance(t, dict):
            continue
        if t.get("in_progress") is True:
            continue
        metadata = t.get("metadata")
        if isinstance(metadata, dict) and metadata.get("status") == "in_progress":
            continue
        tid = str(t.get("id") or "").strip()
        return tid or None
    return None


@dataclass(frozen=True)
class DurableTurnTail:
    """The durable log's turn count and last settled turn id at log head ``seq``.

    ``complete`` is False when the log had no boundary row for more than the fold's
    pending limit, so the oldest raw frames were not reduced and ``turns`` undercounts.
    """

    seq: int
    turns: int
    tail_id: str | None
    complete: bool


@dataclass
class TurnTailFold:
    """Incremental turn count and settled tail over one session's durable log."""

    max_pending: int
    settled_turns: int = 0
    settled_tail_id: str | None = None
    resume_after: int = 0
    truncated: bool = False

    def settle(self, pending: list[SessionLogEntry]) -> list[SessionLogEntry]:
        """Settle ``pending`` up to its last boundary row; return the unsettled rest.

        Returns ``pending`` unchanged when it has no boundary, keeping only its newest
        ``max_pending`` frames (and marking the fold truncated) once it grows past that.
        """
        settled = settle_pending(pending)
        if settled is None:
            if len(pending) > self.max_pending:
                self.truncated = True
                return pending[-self.max_pending :]
            return pending
        turns, boundary_seq, rest = settled
        self.settled_turns += len(turns)
        self.settled_tail_id = last_settled_turn_id(turns) or self.settled_tail_id
        self.resume_after = boundary_seq
        return rest

    def summary(self, pending: list[SessionLogEntry], seq: int) -> DurableTurnTail:
        """The settled count and tail plus the folded (uncommitted) live tail ``pending``."""
        turns: list[dict[str, Any]] = rebuild_turns(pending).turns if pending else []
        return DurableTurnTail(
            seq=seq,
            turns=self.settled_turns + len(turns),
            tail_id=last_settled_turn_id(turns) or self.settled_tail_id,
            complete=not self.truncated,
        )
