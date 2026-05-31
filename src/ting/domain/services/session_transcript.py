"""Reusable transcript attachment for Linear issues.

Fetches a Volundr session conversation, formats it as markdown, and
attaches it to a tracker issue.  Used by both the review engine
(reviewer transcripts) and working-session flows.
"""

from __future__ import annotations

import logging

from ting.ports.tracker import TrackerPort
from ting.ports.volundr import VolundrFactory

logger = logging.getLogger(__name__)


def _format_transcript(turns: list[dict], title_prefix: str, run_name: str) -> tuple[str, str]:
    """Return ``(title, markdown_body)`` for a transcript document."""
    title = f"{title_prefix} — {run_name}"
    lines = [f"# {title_prefix}", ""]
    for turn in turns:
        role = turn.get("role", "unknown").capitalize()
        content = turn.get("content", "")
        lines.append(f"### {role}")
        lines.append(content)
        lines.append("")
        lines.append("---")
        lines.append("")
    return title, "\n".join(lines)


async def attach_session_transcript(
    volundr_factory: VolundrFactory,
    tracker: TrackerPort,
    tracker_id: str,
    owner_id: str,
    session_id: str,
    title_prefix: str,
    run_name: str,
) -> None:
    """Fetch a session conversation and attach it as a tracker document.

    Parameters
    ----------
    volundr_factory:
        Factory that resolves per-owner Volundr adapters.
    tracker:
        Tracker port used to attach the document.
    tracker_id:
        Issue identifier in the tracker (e.g. Linear issue ID).
    owner_id:
        Owner whose Volundr adapter should be used.
    session_id:
        Volundr session whose conversation will be fetched.
    title_prefix:
        Human-readable prefix such as ``"Review Transcript"`` or
        ``"Working Session Transcript"``.
    run_name:
        Run name appended to the document title.
    """
    try:
        adapters = await volundr_factory.for_owner(owner_id)
        if not adapters:
            logger.warning(
                "No Volundr adapter for owner %s — cannot fetch transcript",
                owner_id[:8],
            )
            return

        conversation = await adapters[0].get_conversation(session_id)
        turns = conversation.get("turns", [])
        title, body = _format_transcript(turns, title_prefix, run_name)

        await tracker.attach_issue_document(tracker_id, title, body)
        logger.info(
            "Attached %s (%d turns) to %s",
            title_prefix,
            len(turns),
            tracker_id,
        )
    except Exception:
        logger.warning(
            "Failed to attach %s for %s",
            title_prefix,
            tracker_id,
            exc_info=True,
        )
