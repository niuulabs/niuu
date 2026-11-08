"""Ordinary CLI commands must not initialize services or the terminal UI."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "arguments",
    [["--version"], ["platform", "--help"], ["platform", "status"], ["sessions", "--help"]],
)
def test_cli_defers_service_and_tui_imports(tmp_path, arguments):
    script = """
import runpy
import sys
try:
    runpy.run_module('niuu', run_name='__main__')
except SystemExit as exc:
    assert exc.code in (None, 0), exc.code
for module in ('fastapi', 'textual', 'integrations.app', 'volundr.domain.models'):
    assert module not in sys.modules, module
"""
    result = subprocess.run(
        [sys.executable, "-c", script, *arguments],
        env={**os.environ, "NIUU_CONFIG": str(tmp_path / "missing.yaml")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_lazy_package_exports_preserve_public_objects():
    import integrations
    import volundr.domain as domain
    from integrations.app import create_app
    from integrations.plugin import IntegrationsPlugin
    from volundr.domain.models import Session, SessionStatus
    from volundr.domain.ports import PodManager, SessionRepository
    from volundr.domain.services import SessionService

    assert integrations.create_app is create_app
    assert integrations.IntegrationsPlugin is IntegrationsPlugin
    for name, value in {
        "Session": Session,
        "SessionStatus": SessionStatus,
        "PodManager": PodManager,
        "SessionRepository": SessionRepository,
        "SessionService": SessionService,
    }.items():
        assert getattr(domain, name) is value
    with pytest.raises(AttributeError):
        getattr(integrations, "missing")
    with pytest.raises(AttributeError):
        getattr(domain, "missing")
