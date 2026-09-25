"""Tests for SleipnirEventTrigger's payload_filter hook."""

from __future__ import annotations

import json
from types import SimpleNamespace

from ravn.adapters.triggers.sleipnir import SleipnirEventTrigger


def _message(payload: dict, routing_key: str = "github.pr.opened") -> SimpleNamespace:
    return SimpleNamespace(body=json.dumps(payload).encode(), routing_key=routing_key)


async def _enqueue_collector():
    enqueued = []

    async def enqueue(task):
        enqueued.append(task)
        return True

    return enqueue, enqueued


class TestPayloadFilter:
    async def test_no_filter_enqueues_every_message(self) -> None:
        enqueue, enqueued = await _enqueue_collector()
        trigger = SleipnirEventTrigger(name="t1", pattern="github.pr.opened", context_template="hi")
        await trigger._handle_message(_message({"repo": "org/a"}), enqueue)
        assert len(enqueued) == 1

    async def test_filter_returning_true_enqueues(self) -> None:
        enqueue, enqueued = await _enqueue_collector()
        trigger = SleipnirEventTrigger(
            name="t1",
            pattern="github.pr.opened",
            context_template="hi",
            payload_filter=lambda payload: payload.get("repo") == "org/a",
        )
        await trigger._handle_message(_message({"repo": "org/a"}), enqueue)
        assert len(enqueued) == 1

    async def test_filter_returning_false_does_not_enqueue(self) -> None:
        enqueue, enqueued = await _enqueue_collector()
        trigger = SleipnirEventTrigger(
            name="t1",
            pattern="github.pr.opened",
            context_template="hi",
            payload_filter=lambda payload: payload.get("repo") == "org/a",
        )
        await trigger._handle_message(_message({"repo": "org/other"}), enqueue)
        assert enqueued == []

    async def test_filter_evaluated_before_rendering(self) -> None:
        """A filtered-out message must not even attempt template rendering
        (which could fail for content the filter would have rejected)."""
        enqueue, enqueued = await _enqueue_collector()
        trigger = SleipnirEventTrigger(
            name="t1",
            pattern="github.pr.opened",
            context_template="{{ payload.repo.upper() }}",
            payload_filter=lambda payload: False,
        )
        # Payload has no "repo" key — would raise inside the template if
        # rendering were attempted despite the filter rejecting it.
        await trigger._handle_message(_message({}), enqueue)
        assert enqueued == []
