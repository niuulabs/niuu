from __future__ import annotations

from volundr.log_aggregate import aggregate_workspace_logs


def test_aggregate_workspace_logs_parses_multiple_sources_and_filters(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / ".flock" / "logs").mkdir(parents=True, exist_ok=True)
    (workspace / ".services" / "logs").mkdir(parents=True, exist_ok=True)

    (workspace / ".skuld.log").write_text(
        "\n".join(
            [
                "2026-01-01 12:00:00 skuld INFO first broker line",
                "INFO: uvicorn online",
                "plain broker continuation",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / ".flock" / "logs" / "worker-a.log").write_text(
        "2026-01-01 12:00:01 ravn WARNING flock warning\n",
        encoding="utf-8",
    )
    (workspace / ".services" / "logs" / "api.log").write_text(
        "2026-01-01 12:00:02 service ERROR service exploded\n",
        encoding="utf-8",
    )

    payload = aggregate_workspace_logs(
        workspace,
        lines=10,
        level="INFO",
        participants={"skuld", "service:api"},
        query="line",
    )

    assert payload["total"] == 5
    assert payload["filtered"] == 2
    assert payload["returned"] == 2
    assert {item["id"] for item in payload["available_participants"]} == {
        "skuld",
        "worker-a",
        "service:api",
    }
    assert [line["message"] for line in payload["lines"]] == [
        "first broker line",
        "uvicorn online",
    ]


def test_aggregate_workspace_logs_handles_invalid_timestamp_and_default_level(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".skuld.log").write_text(
        "\n".join(
            [
                "2026-01-01 12:00:00 skuld WARN warn alias",
                "2026-99-99 99:99:99 skuld TRACE invalid timestamp falls back",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = aggregate_workspace_logs(workspace, lines=5, level="TRACE")

    assert payload["returned"] == 2
    assert payload["lines"][0]["level"] == "WARN"
    assert payload["lines"][1]["level"] == "INFO"
    assert payload["lines"][1]["message"] == "invalid timestamp falls back"
