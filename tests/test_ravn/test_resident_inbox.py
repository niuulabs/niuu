from __future__ import annotations

from types import SimpleNamespace

import pytest

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.resident_inbox import (
    LocalResidentInbox,
    MimirResidentInbox,
    ResidentInboxClassification,
    ResidentInboxStatus,
)


@pytest.mark.asyncio
async def test_local_inbox_is_durable_and_preserves_acknowledged_replays(tmp_path) -> None:
    first = LocalResidentInbox(tmp_path / "inbox")
    ref = await first.write_directed_message(
        content="Please inspect the current machine state",
        metadata={"message_id": "local-1", "telegram_date": "2026-07-21T12:00:00Z"},
    )

    restarted = LocalResidentInbox(tmp_path / "inbox")
    assert [row[0] for row in await restarted.list_signals()] == [ref]
    assert await restarted.acknowledge((ref,)) == (ref,)

    replay_ref = await restarted.write_directed_message(
        content="Please inspect the current machine state",
        metadata={"message_id": "local-1", "telegram_date": "2026-07-21T12:00:00Z"},
    )
    # A judged slot lives under processed/, so the replay resolves to the record
    # that already exists rather than resurrecting it as pending work.
    assert replay_ref.startswith("resident/inbox/signals/processed/")
    assert await restarted.list_signals(status=ResidentInboxStatus.NEW.value) == []
    assert len(await restarted.list_signals(status=ResidentInboxStatus.REMEMBERED.value)) == 1


@pytest.mark.asyncio
async def test_local_inbox_rejects_refs_outside_its_root(tmp_path) -> None:
    inbox = LocalResidentInbox(tmp_path / "inbox")

    with pytest.raises(ValueError, match="escapes its root"):
        await inbox.acknowledge(("resident/inbox/signals/../../../../outside.md",))


@pytest.mark.asyncio
async def test_local_inbox_retention_never_prunes_unconsumed_signals(tmp_path) -> None:
    import os
    import time

    inbox = LocalResidentInbox(
        tmp_path / "inbox",
        retention_max_pages=1,
        retention_max_age_days=1,
    )
    refs = []
    for index in range(2):
        refs.append(
            await inbox.write_directed_message(
                content=f"unconsumed signal {index}",
                metadata={"message_id": str(index)},
            )
        )

    assert len(await inbox.list_signals(status="", limit=10)) == 2
    stale = tmp_path / "inbox" / refs[0]
    two_days_ago = time.time() - 2 * 86400
    os.utime(stale, (two_days_ago, two_days_ago))
    assert await inbox.prune_signals() == 0

    await inbox.acknowledge(tuple(refs))
    assert await inbox.prune_signals() == 1
    assert len(await inbox.list_signals(status="", limit=10)) == 1


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
        trace_context={"traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"},
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
    assert signal.trace_context == event.trace_context
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


@pytest.mark.asyncio
async def test_triage_and_decision_records_are_written(tmp_path) -> None:
    from ravn.resident_inbox import ResidentInboxTriage

    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)

    triage_ref = await inbox.write_triage(
        ResidentInboxTriage(
            signal_id="sig-1",
            classification=ResidentInboxClassification.TASK_REQUEST.value,
            decision=ResidentInboxStatus.CONVERTED.value,
            reason="asks for work",
            signal_ref="resident/inbox/signals/sig-1.md",
        )
    )
    decision_ref = await inbox.append_decision("processed 1 signal")

    assert triage_ref.startswith("resident/inbox/triage/")
    assert decision_ref.startswith("resident/inbox/decisions/")


@pytest.mark.asyncio
async def test_list_signals_status_filter_excludes_non_matching(tmp_path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    inbox = MimirResidentInbox(mimir)
    await inbox.write_directed_message(
        content="Do some research on pricing",
        metadata={"telegram_message_id": "70"},
    )
    # all NEW; a filter for a different status returns nothing
    assert await inbox.list_signals(status=ResidentInboxStatus.CONVERTED.value) == []
    assert len(await inbox.list_signals(status=ResidentInboxStatus.NEW.value)) == 1


def test_signal_render_parse_round_trip() -> None:
    from ravn.resident_inbox import (
        parse_inbox_signal,
        render_inbox_signal,
        signal_from_directed_message,
    )

    signal = signal_from_directed_message(content="please investigate the resin supplier")
    restored = parse_inbox_signal(render_inbox_signal(signal))
    assert restored is not None
    assert restored.summary == signal.summary
    assert restored.classification == signal.classification
    # operator messages with no telegram id still get a stable id
    assert signal.id.startswith("operator-message-")


def test_parse_inbox_signal_without_json_block_returns_none() -> None:
    from ravn.resident_inbox import parse_inbox_signal

    assert parse_inbox_signal("# Just a heading\n\nno embedded json here") is None


# ---------------------------------------------------------------------------
# Signal retention (NIU-1118 follow-up): the inbox is a rolling working set
# ---------------------------------------------------------------------------


def _make_inbox(tmp_path, **retention):
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    return MimirResidentInbox(mimir, **retention), mimir


def _signals_dir(tmp_path):
    return tmp_path / "mimir" / "wiki" / "resident" / "inbox" / "signals"


@pytest.mark.asyncio
async def test_retention_prunes_pages_beyond_the_count_cap(tmp_path) -> None:
    inbox, _ = _make_inbox(
        tmp_path,
        retention_max_pages=3,
        retention_max_age_days=0,
        retention_sweep_interval_seconds=0.0,
    )
    refs = [
        await inbox.write_directed_message(
            content=f"signal number {index}", metadata={"telegram_message_id": str(index)}
        )
        for index in range(6)
    ]

    assert len(list(_signals_dir(tmp_path).glob("*.md"))) == 6
    await inbox.acknowledge(tuple(refs))
    assert await inbox.prune_signals() == 3

    assert len(list(_signals_dir(tmp_path).glob("*.md"))) == 3
    index = (tmp_path / "mimir" / "wiki" / "index.md").read_text(encoding="utf-8")
    assert index.count("resident/inbox/signals/") == 3


@pytest.mark.asyncio
async def test_retention_prunes_pages_older_than_max_age(tmp_path) -> None:
    import os
    import time

    inbox, _ = _make_inbox(
        tmp_path,
        retention_max_pages=0,
        retention_max_age_days=1.0,
        retention_sweep_interval_seconds=0.0,
    )
    await inbox.write_directed_message(
        content="stale signal",
        metadata={"telegram_message_id": "old"},
    )
    stale = next(iter(_signals_dir(tmp_path).glob("*.md")))
    ref = str(stale.relative_to(tmp_path / "mimir" / "wiki"))
    await inbox.acknowledge((ref,))
    two_days_ago = time.time() - 2 * 86400
    os.utime(stale, (two_days_ago, two_days_ago))

    pruned = await inbox.prune_signals()

    assert pruned == 1
    assert not stale.exists()


@pytest.mark.asyncio
async def test_retention_disabled_keeps_everything(tmp_path) -> None:
    inbox, _ = _make_inbox(
        tmp_path,
        retention_max_pages=0,
        retention_max_age_days=0,
        retention_sweep_interval_seconds=0.0,
    )
    for index in range(4):
        await inbox.write_directed_message(
            content=f"signal number {index}",
            metadata={"telegram_message_id": str(index)},
        )

    assert len(list(_signals_dir(tmp_path).glob("*.md"))) == 4


@pytest.mark.asyncio
async def test_mimir_retention_never_prunes_unconsumed_signals(tmp_path) -> None:
    inbox, _ = _make_inbox(
        tmp_path,
        retention_max_pages=1,
        retention_max_age_days=0,
        retention_sweep_interval_seconds=0.0,
    )
    for index in range(4):
        await inbox.write_directed_message(
            content=f"unconsumed signal {index}",
            metadata={"telegram_message_id": str(index)},
        )

    assert await inbox.prune_signals() == 0
    assert len(list(_signals_dir(tmp_path).glob("*.md"))) == 4


@pytest.mark.asyncio
async def test_retention_sweeps_are_throttled(tmp_path) -> None:
    inbox, _ = _make_inbox(
        tmp_path,
        retention_max_pages=1,
        retention_max_age_days=0,
        retention_sweep_interval_seconds=0.05,
    )
    refs = []
    for index in range(4):
        refs.append(
            await inbox.write_directed_message(
                content=f"signal number {index}",
                metadata={"telegram_message_id": str(index)},
            )
        )

    # The immediate write path remains throttled, but a deferred sweep makes
    # the configured cap true after the records become processed, even if no
    # later signal arrives.
    assert len(list(_signals_dir(tmp_path).glob("*.md"))) > 1
    await inbox.acknowledge(tuple(refs))
    import asyncio

    await asyncio.sleep(0.08)
    assert len(list(_signals_dir(tmp_path).glob("*.md"))) == 1


@pytest.mark.asyncio
async def test_retention_without_filesystem_root_warns_and_skips(caplog) -> None:
    import logging

    class NoFsMimir:
        def filesystem_root(self):
            return None

    inbox = MimirResidentInbox(NoFsMimir())
    with caplog.at_level(logging.WARNING, logger="ravn.resident_inbox.backend"):
        assert await inbox.prune_signals() == 0
        assert await inbox.prune_signals() == 0

    warnings = [r for r in caplog.records if "not filesystem-backed" in r.message]
    assert len(warnings) == 1
