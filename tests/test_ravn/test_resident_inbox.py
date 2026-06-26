from __future__ import annotations

from types import SimpleNamespace

import pytest

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.resident_inbox import (
    MimirResidentInbox,
    ResidentInboxClassification,
    ResidentInboxStatus,
)


@pytest.mark.asyncio
async def test_directed_message_becomes_resident_inbox_signal(tmp_path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)

    ref = await inbox.write_directed_message(
        content="Here is a public source for later: https://example.com/kvm-listing",
        metadata={"telegram_message_id": "42", "telegram_date": "2026-06-22T12:30:00Z"},
    )
    rows = await inbox.list_signals(limit=5)

    assert ref.startswith("resident/inbox/signals/")
    assert len(rows) == 1
    _path, signal = rows[0]
    assert signal.classification == ResidentInboxClassification.SOURCE_EVIDENCE.value
    assert signal.status == ResidentInboxStatus.NEW.value
    assert "https://example.com/kvm-listing" in signal.summary


@pytest.mark.asyncio
async def test_research_directed_message_becomes_task_request(tmp_path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)

    await inbox.write_directed_message(
        content="Do some research on Kanuck Valley Models",
        metadata={"telegram_message_id": "45", "telegram_date": "2026-06-22T12:32:00Z"},
    )
    _path, signal = (await inbox.list_signals(limit=1))[0]

    assert signal.classification == ResidentInboxClassification.TASK_REQUEST.value
    assert signal.reason == "message asks for work"


@pytest.mark.asyncio
async def test_environment_event_becomes_resident_inbox_signal(tmp_path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)

    event = SimpleNamespace(
        event_id="evt-1",
        event_type="environment.signal",
        correlation_id="corr-1",
        timestamp="2026-06-22T12:33:00Z",
        summary="Printer queue stalled",
        payload={
            "severity": "warning",
            "data": {
                "source_id": "octoprint",
                "raw_payload_ref": "raw/evt-1",
                "message": "Printer queue stalled",
            },
        },
    )

    ref = await inbox.write_event(event)
    rows = await inbox.list_signals(limit=5)

    assert ref.startswith("resident/inbox/signals/")
    assert len(rows) == 1
    _path, signal = rows[0]
    assert signal.id == "evt-1"
    assert signal.source == "octoprint"
    assert signal.kind == "environment.signal"
    assert "Printer queue stalled" in signal.summary
    assert signal.raw_ref == "raw/evt-1"
    assert signal.observed_at == "2026-06-22T12:33:00Z"
    assert signal.status == ResidentInboxStatus.NEW.value


@pytest.mark.asyncio
async def test_replayed_directed_message_is_not_duplicated(tmp_path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)
    metadata = {"telegram_message_id": "43", "telegram_date": "2026-06-22T12:31:00Z"}

    first = await inbox.write_directed_message(
        content="Here is a public source for the KVM Etsy workstream: https://example.com/kvm-etsy",
        metadata=metadata,
    )
    replay = await inbox.write_directed_message(
        content="Here is a public source for the KVM Etsy workstream: https://example.com/kvm-etsy",
        metadata=metadata,
    )
    rows = await inbox.list_signals(status="", limit=5)

    assert replay == first
    assert len(rows) == 1
