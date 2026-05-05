from pathlib import Path

from skuld.log_aggregate import aggregate_workspace_logs


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_aggregate_workspace_logs_interleaves_skuld_and_flock_participants(tmp_path: Path) -> None:
    _write(
        tmp_path / ".skuld.log",
        "\n".join(
            [
                "2026-05-01 15:19:48,121 - skuld.broker - INFO - Starting Skuld broker",
                "INFO:     Started server process [76537]",
            ]
        ),
    )
    _write(
        tmp_path / ".flock" / "logs" / "coder.log",
        "\n".join(
            [
                "2026-05-01 15:19:51,232 ravn.cli.commands INFO "
                "mesh: received outcome event_type=code.requested",
                "2026-05-01 15:19:58,326 ravn.drive_loop ERROR drive_loop: task failed",
            ]
        ),
    )

    payload = aggregate_workspace_logs(tmp_path, lines=10, level="DEBUG")

    assert [participant["id"] for participant in payload["available_participants"]] == [
        "skuld",
        "coder",
    ]
    lines = payload["lines"]
    assert len(lines) == 4
    assert lines[0]["participant"] == "skuld"
    assert lines[1]["participant"] == "skuld"
    assert lines[1]["source"] == "uvicorn"
    assert lines[2]["participant"] == "coder"
    assert lines[3]["level"] == "ERROR"


def test_aggregate_workspace_logs_filters_by_participant_query_and_level(tmp_path: Path) -> None:
    _write(
        tmp_path / ".skuld.log",
        "2026-05-01 15:19:48,121 - skuld.broker - INFO - Starting Skuld broker\n",
    )
    _write(
        tmp_path / ".flock" / "logs" / "coder.log",
        "\n".join(
            [
                "2026-05-01 15:19:51,232 ravn.cli.commands INFO "
                "mesh: received outcome event_type=code.requested",
                "2026-05-01 15:19:58,326 ravn.drive_loop ERROR "
                "drive_loop: task failed after 3 retries",
            ]
        ),
    )

    payload = aggregate_workspace_logs(
        tmp_path,
        lines=10,
        level="ERROR",
        participants={"coder"},
        query="retries",
    )

    assert payload["total"] == 3
    assert payload["filtered"] == 1
    assert payload["returned"] == 1
    assert payload["lines"][0]["participant"] == "coder"
    assert payload["lines"][0]["message"].endswith("failed after 3 retries")
