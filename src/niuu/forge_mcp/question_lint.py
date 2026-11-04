"""The question standard, enforced on agent notifications (LEX-1671, LEX-1597 point 3).

A ``decision`` (the user must choose) or ``attention`` (the user must act) is a question.
Before ``notify`` records one:

* with no ``ask``, no question in its title and no options listed in its body, it is not
  a question at all: it is recorded as ``info`` and the tool result says why;
* with an ``ask`` whose choices have no recommended option, or whose items have no
  default, it is recorded as sent and the tool result warns.

Both warnings quote :data:`~niuu.forge_mcp.guidance.QUESTION_STANDARD`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from niuu.domain.notifications import NotificationDraft, NotificationKind
from niuu.forge_mcp.guidance import QUESTION_STANDARD

logger = logging.getLogger(__name__)

#: Kinds that ask the user to choose or act, and so must be questions.
QUESTION_KINDS = frozenset({NotificationKind.DECISION, NotificationKind.ATTENTION})

# A body "lists options" when two or more lines start like list entries:
# "- …", "* …", "• …", "1. …", "2) …", "A. …", "b) …".
_OPTION_LINE = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)]|[A-Da-d][.)])\s+\S", re.MULTILINE)


@dataclass(frozen=True)
class LintedNotification:
    draft: NotificationDraft
    warnings: tuple[str, ...] = ()
    downgraded_from: NotificationKind | None = None


def _lists_options(body: str) -> bool:
    return len(_OPTION_LINE.findall(body)) >= 2


def lint_question(draft: NotificationDraft) -> LintedNotification:
    """Apply the question standard to an agent's notification draft."""
    if draft.kind not in QUESTION_KINDS:
        return LintedNotification(draft)
    if draft.ask is None:
        if "?" in draft.title or _lists_options(draft.body):
            return LintedNotification(draft)
        warning = (
            f"Recorded as info, not {draft.kind.value}: a {draft.kind.value} notification "
            "must ask the user something, and this one has no ask, no question in its "
            "title and no options in its body. The standard: " + QUESTION_STANDARD
        )
        logger.warning(
            "notify: %s without a question downgraded to info: %r", draft.kind, draft.title
        )
        return LintedNotification(
            draft.model_copy(update={"kind": NotificationKind.INFO}),
            (warning,),
            downgraded_from=draft.kind,
        )
    choices = [item for item in draft.ask.items if item.kind == "question" and item.options]
    missing: list[str] = []
    no_recommendation = [item.id for item in choices if item.recommended_option is None]
    if no_recommendation:
        missing.append("no option marked recommended=true in " + ", ".join(no_recommendation))
    no_default = [item.id for item in draft.ask.items if item.default is None]
    if no_default:
        missing.append("no default {option, after} in " + ", ".join(no_default))
    if not missing:
        return LintedNotification(draft)
    warning = (
        f"Recorded as sent, but the question is incomplete ({'; '.join(missing)}). "
        "The standard: " + QUESTION_STANDARD
    )
    return LintedNotification(draft, (warning,))
