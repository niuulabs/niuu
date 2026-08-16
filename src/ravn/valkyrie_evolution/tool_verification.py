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

import ast
import re
import shutil
import subprocess
import symtable
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ravn.valkyrie_evolution.tool_runtime import (
    pip_install_argv,
    provision_env,
    sandbox_env,
    tool_venv_python,
    venv_create_argv,
)

#: Persistent uv cache for ephemeral verification venvs, under the system
#: tempdir (same filesystem as the venvs, so hardlinks work). The venvs are
#: throwaway; the cache is what makes attempt 2..N and the next verify cheap.
_VERIFY_CACHE_ROOT = Path(tempfile.gettempdir()) / "ravn-verify"

#: How long a single verification test run may take before it is killed.
DEFAULT_VERIFY_TIMEOUT_SECONDS = 120

#: How long ``pip install`` of the tool's requirements may take.
DEFAULT_PIP_TIMEOUT_SECONDS = 300

#: How long ``python -m venv`` creation may take.
DEFAULT_VENV_TIMEOUT_SECONDS = 120

#: "No module named 'X'" / "No module named X" — capture the dotted module path.
_MISSING_MODULE_RE = re.compile(r"No module named ['\"]?([\w.]+)['\"]?")


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of one independent-venv verification of a learned tool."""

    ok: bool
    logs: str
    missing_module: str | None = None


def static_defects(tool_code: str, requirements: Sequence[str] = ()) -> list[str]:
    """Return defects visible without running *tool_code*.

    Two failures that a test run cannot be relied on to surface:

    * **Unresolvable imports.** Residents reach for a host SDK that does not
      exist — ``from ravn.sdk import tool`` — inside ``try/except Exception``,
      so the ImportError is swallowed and the tool returns ``None`` while
      reporting success. Nothing in a test run distinguishes that from a tool
      whose answer is legitimately empty.
    * **Free variables.** A name the module never binds raises only on the
      branch that reaches it, so a test exercising any other path passes.

    Scope analysis comes from :mod:`symtable` — the compiler's own — rather
    than a hand-rolled AST walk, which gets ``except X as e`` and
    comprehension targets wrong in exactly the way that produces false
    positives on correct code.
    """
    try:
        tree = ast.parse(tool_code)
        table = symtable.symtable(tool_code, "<learned_tool>", "exec")
    except SyntaxError as exc:
        return [f"tool code does not parse: {exc}"]

    defects: list[str] = []
    allowed = {
        r.split("[")[0].split("==")[0].split(">")[0].split("<")[0].strip().replace("-", "_")
        for r in requirements
    }
    for node in ast.walk(tree):
        roots: list[str] = []
        if isinstance(node, ast.Import):
            roots = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots = [node.module.split(".")[0]]
        for root in roots:
            # ``ravn`` is the host SDK the sandbox injects (tool_runtime's
            # HOST_SDK_MODULE), not something pip provides.
            if root in sys.stdlib_module_names or root in allowed or root == "ravn":
                continue
            defects.append(
                f"imports {root!r}, which is neither in the standard library nor in "
                f"this tool's requirements — it will not exist where the tool runs"
            )

    known = set(dir(__import__("builtins")))

    def binds(sym: symtable.Symbol) -> bool:
        # A module-level ``def``/``class`` is a namespace, ``import x`` is an
        # import; neither reports as "assigned", so all four have to count.
        return bool(
            sym.is_assigned() or sym.is_imported() or sym.is_namespace() or sym.is_parameter()
        )

    known |= {sym.get_name() for sym in table.get_symbols() if binds(sym)}

    def walk(scope: symtable.SymbolTable) -> None:
        for sym in scope.get_symbols():
            if binds(sym) or not sym.is_referenced():
                continue
            if sym.get_name() in known:
                continue
            # Free at every enclosing scope too: symtable resolves closures, so
            # what is left genuinely resolves to nothing at runtime.
            if sym.is_global() or sym.is_free():
                defects.append(
                    f"uses {sym.get_name()!r}, which the tool never defines, imports or "
                    f"receives as an argument"
                )
        for child in scope.get_children():
            walk(child)

    walk(table)
    return sorted(dict.fromkeys(defects))


def first_undeclared_import(tool_code: str, requirements: Sequence[str] = ()) -> str | None:
    """Return the first import that is neither stdlib, declared, nor host-provided.

    The dependency-heal loop installs this and retries, which is the whole
    reason an undeclared import must not be reported as a flat static failure:
    a tool that simply forgot to list ``requests`` is repairable.
    """
    try:
        tree = ast.parse(tool_code)
    except SyntaxError:
        return None
    allowed = {
        r.split("[")[0].split("==")[0].split(">")[0].split("<")[0].strip().replace("-", "_")
        for r in requirements
    }
    for node in ast.walk(tree):
        roots: list[str] = []
        if isinstance(node, ast.Import):
            roots = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots = [node.module.split(".")[0]]
        for root in roots:
            if root in sys.stdlib_module_names or root in allowed or root == "ravn":
                continue
            return root
    return None


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
    """Scrubbed environment for a verification subprocess.

    The ONE sandbox env policy lives in ``tool_runtime.sandbox_env`` —
    verification and execution share it so the two boundaries never drift.
    """
    return sandbox_env()


#: The one venv-interpreter path convention, shared with tool_runtime.
_venv_python = tool_venv_python


_TEST_RUNNER = """
import importlib.util
import inspect
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
parameterized = [test.__name__ for test in tests if inspect.signature(test).parameters]
if parameterized:
    raise TypeError(
        "verification tests must be zero-argument callables; unsupported parameters in: "
        + ", ".join(parameterized)
    )
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
    # Before spending a venv on it: defects a test run cannot be relied on to
    # surface. A tool that swallows an ImportError for a host SDK that does not
    # exist passes any test whose assertions tolerate an empty answer, and with
    # no test_code at all nothing runs against it whatsoever.
    defects = static_defects(tool_code, requirements)
    if defects:
        return VerificationResult(
            ok=False,
            logs="static verification failed:\n" + "\n".join(f"  - {d}" for d in defects),
            # An undeclared import is repairable: the heal loop installs it and
            # retries. Reporting it here keeps that path working instead of
            # turning a forgotten requirement into a dead build.
            missing_module=first_undeclared_import(tool_code, requirements),
        )
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
    """Create the venv (uv-first, shared cache). Failure result, or None on success."""
    try:
        subprocess.run(  # noqa: S603
            venv_create_argv(venv_dir),
            check=True,
            capture_output=True,
            text=True,
            env=provision_env(_VERIFY_CACHE_ROOT),
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
    """Install requirements into the venv (uv-first). Failure result, or None."""
    try:
        completed = subprocess.run(  # noqa: S603
            pip_install_argv(python, requirements),
            check=False,
            capture_output=True,
            text=True,
            env={**env, **provision_env(_VERIFY_CACHE_ROOT)},
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
