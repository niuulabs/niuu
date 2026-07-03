"""Independent verification of a learned tool in a throwaway virtualenv.

Phase 2 of the tool-build spine: never trust the builder's own "it works".
When a tool build hands back code, a self-contained test module, and a pip
requirement list, this module re-verifies the artifact from scratch — in a
fresh ``python -m venv`` with its dependencies installed and an explicit,
scrubbed environment — before the build_tool install path ever registers it.

The verifier is deliberately standalone and importable: the peer
re-verification path (Phase 6) reuses
:func:`verify_learned_tool_in_ephemeral_venv` directly, so its signature stays
keyword-only and free of build_tool internals.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

#: How long a single verification test run may take before it is killed.
DEFAULT_VERIFY_TIMEOUT_SECONDS = 120

#: How long ``pip install`` of the tool's requirements may take.
DEFAULT_PIP_TIMEOUT_SECONDS = 300

#: How long ``python -m venv`` creation may take.
DEFAULT_VENV_TIMEOUT_SECONDS = 120

#: Environment variables an ephemeral-venv subprocess is allowed to inherit.
#: Mirrors the tool_runtime sandbox: a verification run must never see the
#: resident's ambient environment (bearer tokens, PATs, cloud credentials).
_VERIFY_ENV_PASSTHROUGH = ("PATH", "SYSTEMROOT", "LANG", "LC_ALL", "LC_CTYPE", "TZ")

#: "No module named 'X'" / "No module named X" — capture the dotted module path.
_MISSING_MODULE_RE = re.compile(r"No module named ['\"]?([\w.]+)['\"]?")


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of one independent-venv verification of a learned tool."""

    ok: bool
    logs: str
    missing_module: str | None = None


def parse_missing_module(text: str) -> str | None:
    """Extract the top-level pip package name from a ModuleNotFoundError.

    ``"No module named 'requests.sessions'"`` -> ``"requests"``. Returns None
    when the text carries no missing-module signature.
    """
    match = _MISSING_MODULE_RE.search(text or "")
    if match is None:
        return None
    top_level = match.group(1).split(".")[0]
    if not top_level:
        return None
    return top_level


def _verify_env() -> dict[str, str]:
    """Minimal, scrubbed environment for a verification subprocess."""
    env = {key: os.environ[key] for key in _VERIFY_ENV_PASSTHROUGH if key in os.environ}
    env.setdefault("PATH", os.defpath)
    return env


def _venv_python(venv_dir: Path) -> Path:
    """Return the interpreter path inside a freshly created venv."""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


_TEST_RUNNER = """
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("_verify_tool", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.modules["_verify_tool"] = module

spec_t = importlib.util.spec_from_file_location("_verify_test", sys.argv[2])
test_module = importlib.util.module_from_spec(spec_t)
spec_t.loader.exec_module(test_module)

tests = [
    getattr(test_module, name)
    for name in dir(test_module)
    if name.startswith("test") and callable(getattr(test_module, name))
]
for test in tests:
    test()
print("verify: ran %d test callable(s)" % len(tests))
"""


def verify_learned_tool_in_ephemeral_venv(
    *,
    tool_name: str,
    tool_code: str,
    test_code: str,
    requirements: list[str],
    entry_point: str = "run",
    timeout_seconds: int = DEFAULT_VERIFY_TIMEOUT_SECONDS,
    pip_timeout_seconds: int = DEFAULT_PIP_TIMEOUT_SECONDS,
    venv_timeout_seconds: int = DEFAULT_VENV_TIMEOUT_SECONDS,
) -> VerificationResult:
    """Verify a learned tool from scratch in a throwaway virtualenv.

    Creates a temp dir + ``python -m venv``, pip-installs the tool's
    ``requirements`` into it (bounded timeout), writes ``tool_code`` to
    ``{tool_name}.py`` and ``test_code`` to a runner, then runs the test with
    the venv interpreter (cwd = temp dir). Exit 0 = pass. The temp dir is
    always cleaned up.

    An empty ``test_code`` is not a hard failure: the artifact still cleared
    the structural gate in review, so verification returns ``ok=True`` and
    records that only structural validation ran. A builder that produced no
    tests is a weaker signal, not a rejection.
    """
    del entry_point  # accepted for a stable signature; the test module drives the run.
    if not test_code.strip():
        return VerificationResult(
            ok=True,
            logs="no test_code supplied; structural validation only",
        )

    tempdir = Path(tempfile.mkdtemp(prefix="verify-learned-tool-"))
    try:
        return _run_verification(
            tempdir=tempdir,
            tool_name=tool_name,
            tool_code=tool_code,
            test_code=test_code,
            requirements=requirements,
            timeout_seconds=timeout_seconds,
            pip_timeout_seconds=pip_timeout_seconds,
            venv_timeout_seconds=venv_timeout_seconds,
        )
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)


def _run_verification(
    *,
    tempdir: Path,
    tool_name: str,
    tool_code: str,
    test_code: str,
    requirements: list[str],
    timeout_seconds: int,
    pip_timeout_seconds: int,
    venv_timeout_seconds: int,
) -> VerificationResult:
    venv_dir = tempdir / ".venv"
    env = _verify_env()

    create = _create_venv(venv_dir, timeout_seconds=venv_timeout_seconds)
    if create is not None:
        return create

    python = _venv_python(venv_dir)
    if requirements:
        install = _pip_install(
            python,
            requirements,
            env=env,
            timeout_seconds=pip_timeout_seconds,
        )
        if install is not None:
            return install

    module_name = _module_name_for_tool(tool_name)
    tool_path = tempdir / f"{module_name}.py"
    test_path = tempdir / "_verify_test.py"
    runner_path = tempdir / "_verify_runner.py"
    tool_path.write_text(tool_code, encoding="utf-8")
    test_path.write_text(test_code, encoding="utf-8")
    runner_path.write_text(_TEST_RUNNER, encoding="utf-8")

    return _run_test(
        python,
        tool_path=tool_path,
        test_path=test_path,
        runner_path=runner_path,
        cwd=tempdir,
        env=env,
        timeout_seconds=timeout_seconds,
    )


def _create_venv(venv_dir: Path, *, timeout_seconds: int) -> VerificationResult | None:
    """Create the venv. Returns a failure result, or None on success."""
    try:
        subprocess.run(  # noqa: S603
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
            capture_output=True,
            text=True,
            env=_verify_env(),
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return VerificationResult(
            ok=False,
            logs=f"venv creation timed out after {timeout_seconds}s",
        )
    except subprocess.CalledProcessError as exc:
        return VerificationResult(
            ok=False,
            logs=f"venv creation failed:\n{exc.stdout or ''}\n{exc.stderr or ''}".strip(),
        )
    return None


def _pip_install(
    python: Path,
    requirements: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: int,
) -> VerificationResult | None:
    """Install requirements into the venv. Returns a failure result, or None."""
    try:
        completed = subprocess.run(  # noqa: S603
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", *requirements],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return VerificationResult(
            ok=False,
            logs=f"pip install timed out after {timeout_seconds}s: {', '.join(requirements)}",
        )
    if completed.returncode == 0:
        return None
    logs = f"pip install failed:\n{completed.stdout}\n{completed.stderr}".strip()
    return VerificationResult(
        ok=False,
        logs=logs,
        missing_module=parse_missing_module(logs),
    )


def _run_test(
    python: Path,
    *,
    tool_path: Path,
    test_path: Path,
    runner_path: Path,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
) -> VerificationResult:
    try:
        completed = subprocess.run(  # noqa: S603
            [str(python), str(runner_path), str(tool_path), str(test_path)],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            env=env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return VerificationResult(
            ok=False,
            logs=f"verification test timed out after {timeout_seconds}s",
        )

    logs = f"{completed.stdout}\n{completed.stderr}".strip()
    if completed.returncode == 0:
        return VerificationResult(ok=True, logs=logs or "verification passed")
    return VerificationResult(
        ok=False,
        logs=logs or f"verification test exited with status {completed.returncode}",
        missing_module=parse_missing_module(logs),
    )


def _module_name_for_tool(tool_name: str) -> str:
    """Map a tool name onto an importable module filename stem."""
    stem = tool_name.replace(".", "_").replace("-", "_").strip()
    return stem or "learned_tool"
