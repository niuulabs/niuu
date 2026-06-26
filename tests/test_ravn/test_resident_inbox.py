from __future__ import annotations

import pytest

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.resident_work.local import LocalResidentWorkItemBackend
from ravn.domain.resident_portfolio import ResidentObjectiveStatus
from ravn.resident_inbox import (
    MimirResidentInbox,
    ResidentInboxClassification,
    ResidentInboxConfig,
    ResidentInboxRuntime,
    ResidentInboxStatus,
    signal_from_directed_message,
)
from ravn.resident_opportunity import (
    LocalResidentOpportunityBackend,
    ResidentOpportunityConfig,
    ResidentOpportunityRuntime,
)
from tests.test_ravn.test_resident_state import RecordingGBrainResidentState

MANDATE = (
    "Kanuck Valley Models is my small 3D printing company. "
    "Help it become easier to run, more creative, and more successful."
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
async def test_inbox_runtime_converts_source_signal_to_objective_and_opportunity(tmp_path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)
    work = LocalResidentWorkItemBackend(tmp_path / "portfolio")
    memory = RecordingGBrainResidentState(
        tmp_path / "state",
        mcp_url="http://127.0.0.1:3131/mcp",
        api_token="token",
    )
    signal_ref = await inbox.write_directed_message(
        content="Here is a public source for the KVM Etsy workstream: https://example.com/kvm-etsy",
        metadata={"telegram_message_id": "43", "telegram_date": "2026-06-22T12:31:00Z"},
    )
    source_signals = await inbox.collect(
        mandate=MANDATE,
        domain_model=None,
        objectives=(),
        limit=5,
    )

    run = await ResidentInboxRuntime(
        inbox=inbox,
        work=work,
        memory=memory,
        config=ResidentInboxConfig(max_signals_per_wake=5),
    ).run(MANDATE)
    objectives = await work.list_objectives(MANDATE)
    remaining = await inbox.list_signals(status=ResidentInboxStatus.NEW.value, limit=5)
    opportunity_report = await ResidentOpportunityRuntime(
        backend=work,
        opportunity_backend=LocalResidentOpportunityBackend(tmp_path / "opportunities"),
        sources=(inbox,),
        config=ResidentOpportunityConfig(max_signals=5, max_selected=1, min_total_score=1),
    ).run(MANDATE)

    assert signal_ref.startswith("resident/inbox/signals/")
    assert len(run.processed) == 1
    assert run.processed[0].decision == ResidentInboxStatus.CONVERTED.value
    assert remaining == []
    assert objectives
    assert objectives[0].status == ResidentObjectiveStatus.CANDIDATE.value
    assert signal_ref in objectives[0].source_evidence
    assert source_signals
    assert source_signals[0].evidence_ref.startswith("resident/inbox/signals/")
    assert opportunity_report.signals == ()


@pytest.mark.asyncio
async def test_processed_inbox_signal_is_not_reopened_by_replay(tmp_path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)
    work = LocalResidentWorkItemBackend(tmp_path / "portfolio")
    metadata = {"telegram_message_id": "43", "telegram_date": "2026-06-22T12:31:00Z"}

    signal_ref = await inbox.write_directed_message(
        content="Here is a public source for the KVM Etsy workstream: https://example.com/kvm-etsy",
        metadata=metadata,
    )
    await ResidentInboxRuntime(inbox=inbox, work=work).run(MANDATE)
    replay_ref = await inbox.write_directed_message(
        content="Here is a public source for the KVM Etsy workstream: https://example.com/kvm-etsy",
        metadata=metadata,
    )
    all_rows = await inbox.list_signals(status="", limit=5)
    new_rows = await inbox.list_signals(status=ResidentInboxStatus.NEW.value, limit=5)

    assert replay_ref == signal_ref
    assert len(all_rows) == 1
    assert all_rows[0][1].status == ResidentInboxStatus.CONVERTED.value
    assert new_rows == []


@pytest.mark.asyncio
async def test_inbox_runtime_projects_policy_like_messages_to_memory(tmp_path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)
    work = LocalResidentWorkItemBackend(tmp_path / "portfolio")
    memory = RecordingGBrainResidentState(
        tmp_path / "state",
        mcp_url="http://127.0.0.1:3131/mcp",
        api_token="token",
    )
    await inbox.write_signal(
        signal_from_directed_message(
            "Preference: prefer PLA before resin for quick terrain prototypes.",
            metadata={"telegram_message_id": "44"},
        )
    )

    run = await ResidentInboxRuntime(inbox=inbox, work=work, memory=memory).run(MANDATE)

    assert len(run.processed) == 1
    assert run.processed[0].classification == ResidentInboxClassification.PREFERENCE.value
    assert run.processed[0].decision == ResidentInboxStatus.REMEMBERED.value
    assert any(
        arguments["slug"] == "resident/continuation/policy/resident-inbox-preference"
        for _name, arguments in memory.mcp_calls
    )
