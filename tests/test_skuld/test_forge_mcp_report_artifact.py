"""Broker side of ``report_artifact``: a durable report turn the Forge projector records (F1)."""

from __future__ import annotations

import asyncio

from niuu.domain.artifact_reports import report_from_turn
from tests.test_skuld import test_forge_mcp_broker as broker_tests
from tests.test_skuld.test_forge_mcp_broker import _broker, _call

# The broker suite's fixtures: a fake Forge and a broker flushing its durable log to it.
forge = broker_tests.forge
flushing = broker_tests.flushing

REPORT = {
    "url": "https://claude.ai/artifact/Rep0rt1",
    "title": "Release  notes",
    "provider_version": "1792-ab",
    "version_label": "v3",
    "type": "doc",
}


class TestReportArtifact:
    async def test_committed_once_forge_stores_the_turn(self, flushing, forge) -> None:
        result = await _call(flushing, "report_artifact", REPORT)

        assert result["isError"] is False, result
        payload = result["structuredContent"]
        assert payload["state"] == "committed"
        assert payload["url"] == REPORT["url"]
        assert payload["turn_id"].startswith("ar_")
        [entry] = [e for e in forge.logged if e["payload"].get("type") == "conversation.turn"]
        assert entry["seq"] == payload["session_seq"]
        report = report_from_turn(entry["payload"]["turn"])
        assert report is not None
        assert (report.title, report.version_label, report.type) == ("Release notes", "v3", "doc")
        assert flushing._conversation_turns[-1].id == payload["turn_id"]
        assert flushing.broadcasts[-1]["turn"]["id"] == payload["turn_id"]

    async def test_pending_while_forge_is_down(self, flushing, forge) -> None:
        forge.log_status = 503
        flushing._settings.forge_mcp.notify_confirm_timeout_s = 0.1
        result = await _call(flushing, "report_artifact", REPORT)
        assert result["structuredContent"]["state"] == "pending"
        forge.log_status = 201
        for _ in range(100):
            if forge.logged:
                break
            await asyncio.sleep(0.02)
        assert forge.logged[0]["seq"] == result["structuredContent"]["session_seq"]

    async def test_a_conflicting_seq_is_reported(self, flushing, forge) -> None:
        forge.log_conflicts = [1]
        result = await _call(flushing, "report_artifact", REPORT)
        assert result["isError"] is True
        assert "not stored" in result["content"][0]["text"]

    async def test_no_durable_log(self, tmp_path) -> None:
        broker = _broker(tmp_path, None)
        result = await _call(broker, "report_artifact", REPORT)
        assert result["isError"] is True
        assert "no durable Forge log" in result["content"][0]["text"]
        assert broker._conversation_turns == []

    async def test_refused_append(self, tmp_path, forge) -> None:
        broker = _broker(tmp_path, forge)
        broker._append_turn = lambda turn: None  # type: ignore[method-assign]
        result = await _call(broker, "report_artifact", REPORT)
        assert result["isError"] is True
        assert "refused the report turn" in result["content"][0]["text"]

    async def test_non_claude_url_is_a_validation_error(self, tmp_path, forge) -> None:
        broker = _broker(tmp_path, forge)
        result = await _call(broker, "report_artifact", {**REPORT, "url": "https://x.test/a"})
        assert result["isError"] is True
        assert broker._conversation_turns == []
