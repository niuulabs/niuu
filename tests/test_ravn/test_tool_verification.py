"""Independent verification of learned tools in a throwaway venv (P2)."""

from __future__ import annotations

import subprocess
from typing import Any

import ravn.valkyrie_evolution.tool_verification as verify_mod
from ravn.valkyrie_evolution.tool_verification import (
    VerificationResult,
    _module_name_for_tool,
    parse_missing_module,
    verify_learned_tool_in_ephemeral_venv,
)

_PASSING_TOOL = "def run(payload):\n    return {'echo': payload}\n"
_PASSING_TEST = (
    "import _verify_tool\n\n"
    "def test_run_echoes():\n"
    "    assert _verify_tool.run({'a': 1}) == {'echo': {'a': 1}}\n"
)


def test_parse_missing_module_extracts_top_level_package() -> None:
    text = "ModuleNotFoundError: No module named 'requests.sessions'"
    assert parse_missing_module(text) == "requests"


def test_parse_missing_module_handles_unquoted_and_plain_name() -> None:
    assert parse_missing_module("No module named numpy") == "numpy"
    assert parse_missing_module("No module named 'httpx'") == "httpx"


def test_parse_missing_module_returns_none_without_signature() -> None:
    assert parse_missing_module("everything is fine") is None
    assert parse_missing_module("") is None


def test_empty_test_code_is_a_soft_pass_not_a_failure() -> None:
    result = verify_learned_tool_in_ephemeral_venv(
        tool_name="noop_tool",
        tool_code=_PASSING_TOOL,
        test_code="",
        requirements=[],
    )
    assert isinstance(result, VerificationResult)
    assert result.ok is True
    assert result.missing_module is None
    assert "structural validation only" in result.logs


def test_passing_stdlib_tool_verifies_in_a_real_venv() -> None:
    """End-to-end: a trivial stdlib tool is built and tested in a fresh venv."""
    result = verify_learned_tool_in_ephemeral_venv(
        tool_name="echo_tool",
        tool_code=_PASSING_TOOL,
        test_code=_PASSING_TEST,
        requirements=[],
    )
    assert result.ok is True
    assert result.missing_module is None
    assert "ran 1 test" in result.logs


def test_failing_tool_reports_not_ok() -> None:
    failing_test = (
        "import _verify_tool\n\n"
        "def test_wrong():\n"
        "    assert _verify_tool.run({}) == {'expected': 'other'}\n"
    )
    result = verify_learned_tool_in_ephemeral_venv(
        tool_name="echo_tool",
        tool_code=_PASSING_TOOL,
        test_code=failing_test,
        requirements=[],
    )
    assert result.ok is False
    # An assertion failure carries no missing-module signature.
    assert result.missing_module is None
    assert "AssertionError" in result.logs


def test_pytest_fixture_parameter_is_rejected_with_actionable_error() -> None:
    fixture_test = (
        "import _verify_tool\n\n"
        "def test_run(monkeypatch):\n"
        "    assert _verify_tool.run({}) == {'echo': {}}\n"
    )
    result = verify_learned_tool_in_ephemeral_venv(
        tool_name="echo_tool",
        tool_code=_PASSING_TOOL,
        test_code=fixture_test,
        requirements=[],
    )

    assert result.ok is False
    assert "must be zero-argument callables" in result.logs
    assert "test_run" in result.logs


def test_missing_module_is_surfaced_for_dependency_heal() -> None:
    tool_code = "import totally_absent_pkg\n\ndef run(payload):\n    return {}\n"
    test_code = "import _verify_tool\n\ndef test_import():\n    _verify_tool.run({})\n"
    result = verify_learned_tool_in_ephemeral_venv(
        tool_name="needs_dep_tool",
        tool_code=tool_code,
        test_code=test_code,
        requirements=[],
    )
    assert result.ok is False
    assert result.missing_module == "totally_absent_pkg"


def test_module_name_falls_back_when_name_is_unusable() -> None:
    assert _module_name_for_tool("weird.tool-name") == "weird_tool_name"
    assert _module_name_for_tool("   ") == "learned_tool"


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_subprocess_run(script) -> Any:
    """Build a subprocess.run replacement driven by a per-argv script callable."""

    def _run(argv, **_: Any) -> _FakeCompleted:
        return script(argv)

    return _run


def _venv_succeeds(argv: list[str]) -> _FakeCompleted:
    """Make the venv/pip steps look successful; explode if the test runs."""
    if "venv" in argv:
        return _FakeCompleted(0)
    if "pip" in argv:
        return _FakeCompleted(0)
    return _FakeCompleted(0, stdout="verify: ran 1 test callable(s)")


def test_requirements_are_installed_before_the_test_runs(monkeypatch) -> None:
    seen: list[list[str]] = []

    def script(argv: list[str]) -> _FakeCompleted:
        seen.append(list(argv))
        return _venv_succeeds(argv)

    monkeypatch.setattr(verify_mod.subprocess, "run", _fake_subprocess_run(script))
    result = verify_learned_tool_in_ephemeral_venv(
        tool_name="needs_dep",
        tool_code=_PASSING_TOOL,
        test_code=_PASSING_TEST,
        requirements=["requests==2.0"],
    )
    assert result.ok is True
    assert any("pip" in argv and "requests==2.0" in argv for argv in seen)


def test_pip_install_failure_surfaces_missing_module(monkeypatch) -> None:
    def script(argv: list[str]) -> _FakeCompleted:
        if "venv" in argv:
            return _FakeCompleted(0)
        if "pip" in argv:
            return _FakeCompleted(1, stderr="No module named 'wheelhelper'")
        raise AssertionError("test should not run after a failed install")

    monkeypatch.setattr(verify_mod.subprocess, "run", _fake_subprocess_run(script))
    result = verify_learned_tool_in_ephemeral_venv(
        tool_name="needs_dep",
        tool_code=_PASSING_TOOL,
        test_code=_PASSING_TEST,
        requirements=["broken-dep"],
    )
    assert result.ok is False
    assert "pip install failed" in result.logs
    assert result.missing_module == "wheelhelper"


def test_pip_install_timeout_is_reported(monkeypatch) -> None:
    def _run(argv, **_: Any):
        if "venv" in argv:
            return _FakeCompleted(0)
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(verify_mod.subprocess, "run", _run)
    result = verify_learned_tool_in_ephemeral_venv(
        tool_name="slow_dep",
        tool_code=_PASSING_TOOL,
        test_code=_PASSING_TEST,
        requirements=["slow"],
        pip_timeout_seconds=1,
    )
    assert result.ok is False
    assert "pip install timed out" in result.logs


def test_venv_creation_failure_is_reported(monkeypatch) -> None:
    def _run(argv, **_: Any):
        raise subprocess.CalledProcessError(returncode=1, cmd=argv, stderr="no python")

    monkeypatch.setattr(verify_mod.subprocess, "run", _run)
    result = verify_learned_tool_in_ephemeral_venv(
        tool_name="t",
        tool_code=_PASSING_TOOL,
        test_code=_PASSING_TEST,
        requirements=[],
    )
    assert result.ok is False
    assert "venv creation failed" in result.logs


def test_venv_creation_timeout_is_reported(monkeypatch) -> None:
    def _run(argv, **_: Any):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(verify_mod.subprocess, "run", _run)
    result = verify_learned_tool_in_ephemeral_venv(
        tool_name="t",
        tool_code=_PASSING_TOOL,
        test_code=_PASSING_TEST,
        requirements=[],
        venv_timeout_seconds=1,
    )
    assert result.ok is False
    assert "venv creation timed out" in result.logs


def test_test_run_timeout_is_reported(monkeypatch) -> None:
    def _run(argv, **_: Any):
        if "venv" in argv:
            return _FakeCompleted(0)
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(verify_mod.subprocess, "run", _run)
    result = verify_learned_tool_in_ephemeral_venv(
        tool_name="t",
        tool_code=_PASSING_TOOL,
        test_code=_PASSING_TEST,
        requirements=[],
        timeout_seconds=1,
    )
    assert result.ok is False
    assert "verification test timed out" in result.logs


class TestStaticDefects:
    """Catch what a test run cannot be relied on to surface.

    Residents wrote ``from ravn.sdk import tool`` against an SDK that did not
    exist, wrapped it in ``except Exception: return None``, and shipped tools
    that reported success while doing nothing. 28 of 105 artifacts on one
    resident carried no verification at all; of those that did, the swallow
    meant passing proved nothing.
    """

    def test_unresolvable_import_is_a_defect(self) -> None:
        from ravn.valkyrie_evolution.tool_verification import static_defects

        code = "import nowhere_at_all\ndef run(i):\n    return {}\n"
        assert any("nowhere_at_all" in d for d in static_defects(code))

    def test_declared_requirement_is_not_a_defect(self) -> None:
        from ravn.valkyrie_evolution.tool_verification import static_defects

        code = "import httpx\ndef run(i):\n    return {'t': httpx.get('http://x').text}\n"
        assert static_defects(code, ["httpx"]) == []

    def test_host_sdk_is_allowed_because_the_sandbox_provides_it(self) -> None:
        from ravn.valkyrie_evolution.tool_verification import static_defects

        code = "from ravn.sdk import tool\ndef run(i):\n    return tool.kubernetes_inspect()\n"
        assert static_defects(code) == []

    def test_free_variable_is_a_defect(self) -> None:
        from ravn.valkyrie_evolution.tool_verification import static_defects

        code = "def run(i):\n    return {'v': never_defined(i)}\n"
        assert any("never_defined" in d for d in static_defects(code))

    def test_correct_code_is_clean(self) -> None:
        """No false positives on except-as, comprehensions, closures, walrus."""
        from ravn.valkyrie_evolution.tool_verification import static_defects

        code = (
            "import json, os\n"
            "class C:\n"
            "    def m(self):\n"
            "        return os.sep\n"
            "def _h(v):\n"
            "    return v * 2\n"
            "def run(i):\n"
            "    try:\n"
            "        d = json.loads(i.get('raw', '{}'))\n"
            "    except ValueError as e:\n"
            "        return {'error': str(e)}\n"
            "    if (n := d.get('n')):\n"
            "        return {'v': _h(n), 'c': C().m(), 'l': [x for x in d.get('l', [])]}\n"
            "    return {}\n"
        )
        assert static_defects(code) == []

    def test_undeclared_import_still_reports_a_healable_module(self) -> None:
        """The dependency-heal loop installs this and retries."""
        from ravn.valkyrie_evolution.tool_verification import (
            verify_learned_tool_in_ephemeral_venv,
        )

        result = verify_learned_tool_in_ephemeral_venv(
            tool_name="t",
            tool_code="import somepkg\ndef run(i):\n    return {}\n",
            test_code="",
            requirements=[],
        )
        assert result.ok is False
        assert result.missing_module == "somepkg"

    def test_empty_tests_no_longer_pass_a_broken_tool(self) -> None:
        """The hole: no test_code used to mean an automatic ok=True."""
        from ravn.valkyrie_evolution.tool_verification import (
            verify_learned_tool_in_ephemeral_venv,
        )

        result = verify_learned_tool_in_ephemeral_venv(
            tool_name="t",
            tool_code="def run(i):\n    return {'v': undefined_helper(i)}\n",
            test_code="",
            requirements=[],
        )
        assert result.ok is False
        assert "undefined_helper" in result.logs
