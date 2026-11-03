"""Tell owners, through the notification feed and push, that a resident asks them something.

A resident that needs its owner files an ODIN review item (``answer_operator_question``).
The queue lives in ``niuu-ravn``; nothing there notifies anyone. This relay follows the
queue through an :class:`OdinReviewSource` and records one notification per pending
question for each configured owner, in the same feed and through the same outbox
(rules, sinks, quiet hours, retries) as every other Forge notification, so the owner's
"Needs-input pushes" rule delivers it to their phone.

* **Idempotent.** A notification's identity is (owner, review item); a replay, a restart
  or a second Forge replica records it once.
* **Retired when settled.** Once the item leaves ``pending`` (answered, approved,
  rejected, expired, withdrawn by the resident) the owner's notification is marked read,
  as a session's is when it stops waiting.
* **Backlog.** When the relay first follows the queue for an owner, the questions that
  are already waiting are recorded for the feed but marked feed-only, so they are not
  pushed one by one; one digest notification, which does push, says how many wait.
  Questions asked after that moment push individually, carrying the Reply category and
  the review id and decide path the phone needs to answer inline.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from volundr.domain.odin_review import (
    OperatorQuestion,
    OwnerRelayState,
    digest_body,
    digest_dedupe_key,
    digest_title,
    question_dedupe_key,
    question_metadata,
    question_title,
)
from volundr.domain.odin_review_ports import OdinReviewRelayStateStore, OdinReviewSource
from volundr.domain.services.notifications import NotificationService

logger = logging.getLogger(__name__)


@dataclass
class OdinReviewRelayStats:
    """The relay's health surface: what it did, and whether its last poll worked."""

    polls: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_error: str = ""
    last_success_at: datetime | None = None
    recorded: int = 0
    digests: int = 0
    retired: int = 0
    malformed: int = 0


class OdinReviewNotificationRelay:
    """Follow the ODIN review queue and notify each owner of pending resident questions."""

    def __init__(
        self,
        source: OdinReviewSource,
        state: OdinReviewRelayStateStore,
        notifications: NotificationService,
        *,
        owner_ids: list[str],
        poll_interval_seconds: float,
        push_sink: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not owner_ids or any(not owner.strip() for owner in owner_ids):
            raise ValueError(
                "The ODIN review relay needs owner_ids: the Forge user ids whose feed receives "
                "the questions (notifications.odin_review_relay.owner_ids)"
            )
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._source = source
        self._state = state
        self._notifications = notifications
        self._owner_ids = list(dict.fromkeys(owner_ids))
        self._poll_interval = poll_interval_seconds
        self._push_sink = push_sink
        self._clock = clock or (lambda: datetime.now(UTC))
        self._task: asyncio.Task[None] | None = None
        self.stats = OdinReviewRelayStats()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if self._push_sink is not None:
            # The owners' devices register with whichever Forge their app talks to,
            # which need not be this one, so registering never gave them this Forge's
            # "Needs-input pushes" rule and nothing here was ever pushed. Give it to
            # every configured owner; one who already routes to the sink (or disabled
            # that rule) is left alone. An unconfigured sink raises.
            for owner_id in self._owner_ids:
                await self._notifications.ensure_attention_push_rule(owner_id, self._push_sink)
        self._task = asyncio.create_task(self._run(), name="odin-review-notification-relay")
        logger.info(
            "Following the ODIN review queue for %d owner(s) every %.0fs",
            len(self._owner_ids),
            self._poll_interval,
        )

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self._source.close()

    async def sync(self) -> None:
        """One poll: record new questions, announce the backlog once, retire settled ones."""
        snapshot = await self._source.pending_questions()
        if snapshot.malformed:
            self.stats.malformed += len(snapshot.malformed)
        for owner_id in self._owner_ids:
            await self._sync_owner(owner_id, snapshot.questions)

    async def _run(self) -> None:
        # Level-triggered: every poll re-reads the whole queue and the work it does is
        # idempotent, so a failed poll loses nothing; it is logged at error level, counted
        # in ``stats`` and tried again on the next interval.
        while True:
            self.stats.polls += 1
            try:
                await self.sync()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.stats.failures += 1
                self.stats.consecutive_failures += 1
                self.stats.last_error = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "ODIN review relay poll failed (%d in a row): %s",
                    self.stats.consecutive_failures,
                    self.stats.last_error,
                    exc_info=True,
                )
            else:
                self.stats.consecutive_failures = 0
                self.stats.last_error = ""
                self.stats.last_success_at = self._clock()
            await asyncio.sleep(self._poll_interval)

    async def _sync_owner(self, owner_id: str, questions: list[OperatorQuestion]) -> None:
        state = await self._state.begin_owner(owner_id, self._clock())
        pending_ids = {question.review_id for question in questions}
        recorded = await self._state.recorded_among(owner_id, pending_ids)
        unannounced = sorted(
            (question for question in questions if question.review_id not in recorded),
            key=lambda question: question.requested_at,
        )
        for question in unannounced:
            await self._record_question(owner_id, question, state)
        if not state.digest_recorded:
            await self._record_digest(owner_id, state, questions)
        await self._retire_settled(owner_id, pending_ids)

    async def _record_question(
        self, owner_id: str, question: OperatorQuestion, state: OwnerRelayState
    ) -> None:
        backlog = question.requested_at <= state.watermark_at
        created = await self._notifications.record_system_attention(
            owner_id,
            dedupe_key=question_dedupe_key(owner_id, question.review_id),
            title=question_title(question),
            body=question.question,
            metadata=question_metadata(question, backlog=backlog),
        )
        await self._state.mark_recorded(owner_id, question.review_id)
        if created is None:
            return
        self.stats.recorded += 1
        logger.info(
            "Recorded %s question %s from %s for %s",
            "backlog" if backlog else "new",
            question.review_id,
            question.valkyrie_id,
            owner_id,
        )

    async def _record_digest(
        self, owner_id: str, state: OwnerRelayState, questions: list[OperatorQuestion]
    ) -> None:
        backlog = [q for q in questions if q.requested_at <= state.watermark_at]
        if backlog:
            created = await self._notifications.record_system_attention(
                owner_id,
                dedupe_key=digest_dedupe_key(owner_id),
                title=digest_title(len(backlog)),
                body=digest_body(Counter(question.valkyrie_id for question in backlog)),
                metadata={},
            )
            if created is not None:
                self.stats.digests += 1
        await self._state.mark_digest_recorded(owner_id)

    async def _retire_settled(self, owner_id: str, pending_ids: set[str]) -> None:
        for review_id in sorted(await self._state.open_ids(owner_id) - pending_ids):
            # A listing can miss an item that is still pending (the queue moved between
            # pages); only the item itself says whether it was settled.
            if await self._source.is_pending(review_id):
                continue
            await self._notifications.retire_system_attention(
                owner_id, dedupe_key=question_dedupe_key(owner_id, review_id)
            )
            await self._state.mark_retired(owner_id, review_id)
            self.stats.retired += 1
            logger.info("Retired question %s for %s: no longer pending", review_id, owner_id)
