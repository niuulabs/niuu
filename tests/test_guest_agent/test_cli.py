from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tests.test_guest_agent.conftest import credentials_body
from volundr.guest_agent import cli
from volundr.guest_agent.agent import GuestAgent

REPO_SRC = Path(__file__).resolve().parents[2] / "src"


def patch_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    def build(config, signer):
        return GuestAgent(config, signer, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(cli, "GuestAgent", build)


def test_once_writes_files_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, config_file: Path, tmp_path: Path
) -> None:
    patch_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, json=credentials_body(now=datetime.now(UTC), token="cli-token")
        ),
    )

    assert cli.main(["once", "--config", str(config_file)]) == 0

    assert (tmp_path / "tokens" / "identity" / "token").read_text() == "cli-token"
    assert (tmp_path / "forge-session" / "token").read_text() == "forge-token-1"


def test_once_failure_exits_non_zero_with_one_stderr_line(
    monkeypatch: pytest.MonkeyPatch,
    config_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    patch_transport(
        monkeypatch, lambda request: httpx.Response(401, json={"error": "unauthorized"})
    )

    assert cli.main(["once", "--config", str(config_file)]) == 1

    stderr = capsys.readouterr().err
    assert "niuu-guest-agent: error: Forge rejected the credentials request with HTTP 401" in stderr
    assert not (tmp_path / "tokens").exists()


def test_bad_config_exits_non_zero_naming_the_field(
    tmp_path: Path, config_dict: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dict["listen"]["colour"] = "red"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(config_dict))

    assert cli.main(["once", "--config", str(path)]) == 1

    assert "listen.colour: unknown field" in capsys.readouterr().err


def test_unknown_mode_is_a_usage_error(config_file: Path) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["watch", "--config", str(config_file)])

    assert raised.value.code == 2


def test_module_entry_point_reports_a_missing_config(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "volundr.guest_agent",
            "once",
            "--config",
            str(tmp_path / "no.json"),
        ],
        env={**os.environ, "PYTHONPATH": str(REPO_SRC)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 1
    assert "niuu-guest-agent: error: cannot read the config file" in result.stderr
