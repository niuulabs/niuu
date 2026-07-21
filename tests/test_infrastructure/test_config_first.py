"""AST regression for config-first production environment access."""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
OS_KEYS = {
    "CODEX_HOME",
    "GROK_BIN",
    "HOME",
    "HOST",
    "KUBECONFIG",
    "KUBERNETES_SERVICE_HOST",
    "KUBERNETES_SERVICE_PORT",
    "MYVIMRC",
    "NIUU_HOME",
    "PATH",
    "PORT",
    "PYTHONPATH",
    "WORKERS",
}
BUILD_KEYS = {"NIUU_BUILD_REF", "NIUU_BUILD_SHA", "NIUU_NUITKA_EXTRA_ARGS"}
SECRET_MARKERS = ("API_KEY", "DATABASE_URL", "DSN", "PASSWORD", "SECRET", "TOKEN")
ALLOWLIST = {
    (
        "niuu/adapters/embedded_postgres.py",
        "NIUU_PG_BIN_DIR",
    ): "embedded PostgreSQL executable discovery",
    ("ravn/cli/mimir_bridge.py", "RAVN_MIMIR_PATH"): "CLI companion executable discovery",
    ("ravn/cli/mimir_bridge.py", "RAVN_WORKSPACE_DIR"): "CLI workspace bootstrap",
    ("ravn/main.py", "LOG_LEVEL"): "entrypoint logging bootstrap",
    ("ravn/warden/store.py", "RAVN_WARDENS_DIR"): "standalone local store bootstrap",
    ("skuld/transports/claude_env.py", "SKULD__CLAUDE_AUTH"): "child auth environment filtering",
    ("skuld/transports/tmux_interactive.py", "SKULD__CLAUDE_AUTH"): "tmux child auth filtering",
    (
        "skuld/transports/tmux_interactive.py",
        "SKULD__SESSION__NAME",
    ): "supervisor-injected tmux identity",
    ("skuld/transports/tmux_interactive.py", "SKULD__TMUX_BIN"): "tmux executable discovery",
    (
        "skuld/transports/tmux_interactive.py",
        "SKULD__TMUX_REPL_READY_MARKER",
    ): "tmux child protocol marker",
    ("skuld/transports/tmux_interactive.py", "SKULD__TMUX_SOCKET_DIR"): "tmux OS socket bootstrap",
    (
        "volundr/adapters/outbound/skuld_room.py",
        "OPENSHELL_INTERNAL_GATEWAY_URL",
    ): "provider-injected runtime endpoint",
}


def _reads(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or not node.args
            or not isinstance(node.func, ast.Attribute)
        ):
            continue
        owner = node.func.value
        environ_get = (
            node.func.attr == "get"
            and isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "os"
            and owner.attr == "environ"
        )
        getenv = node.func.attr == "getenv" and isinstance(owner, ast.Name) and owner.id == "os"
        key = node.args[0]
        if (environ_get or getenv) and isinstance(key, ast.Constant) and isinstance(key.value, str):
            result.append((node.lineno, key.value))
    return result


def _intrinsic(key: str) -> bool:
    return (
        key.endswith("_CONFIG")
        or key in OS_KEYS
        or key in BUILD_KEYS
        or any(marker in key for marker in SECRET_MARKERS)
    )


def test_production_environment_reads_are_config_first() -> None:
    violations: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path in SRC_ROOT.rglob("*.py"):
        relative = path.relative_to(SRC_ROOT).as_posix()
        for line, key in _reads(path):
            if _intrinsic(key):
                continue
            entry = (relative, key)
            if entry in ALLOWLIST:
                seen.add(entry)
                continue
            violations.append(f"{relative}:{line}: {key}")
    assert set(ALLOWLIST) == seen, f"stale allowlist: {sorted(set(ALLOWLIST) - seen)}"
    assert not violations, f"move behavioral env reads to typed settings: {violations}"


def test_hardened_modules_have_no_literal_environment_reads() -> None:
    targets = (
        "ravn/api/odin_reviews.py",
        "ravn/adapters/browser/browserbase.py",
        "ravn/adapters/tools/browser.py",
        "skuld/transports/remote_control.py",
    )
    violations = {
        target: _reads(SRC_ROOT / target) for target in targets if _reads(SRC_ROOT / target)
    }
    assert violations == {}
