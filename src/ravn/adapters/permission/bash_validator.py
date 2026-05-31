"""Bash command validation pipeline — 5-stage security analysis.

This module is the **single source of truth** for all bash-validation
constants and helpers.  :mod:`ravn.adapters.permission.enforcer` imports
directly from here; never define a constant in both places.

Each pipeline stage returns a :class:`StageAllow`, :class:`StageDeny`, or
:class:`StageWarn`.  Stages run in order; the first :class:`StageDeny`
causes early exit.  :class:`StageWarn` results accumulate in
:class:`PipelineResult` and are surfaced to the caller without blocking
execution.

Execution order (the spec numbers differ from runtime priority)
---------------------------------------------------------------
1. Destructive-pattern check  — always-blocked, mode-independent.
2. Mode validation            — READ_ONLY whitelist; WORKSPACE_WRITE warns;
                                FULL_ACCESS unrestricted; PROMPT warns.
                                Recursive ``sudo`` unwrapping applied first.
3. Sed in-place check         — ``sed -i`` denied in READ_ONLY, warned otherwise.
4. Path validation            — warn on ``../``, ``~``, ``$HOME``, paths
                                outside the workspace root.
5. Intent classification      — tag with :class:`~ravn.ports.permission.CommandIntent`.

Usage::

    pipeline = BashValidationPipeline()
    result = pipeline.validate("ls /tmp", mode="read_only")
    if not result.allowed:
        raise PermissionError(result.deny_reason)
    for w in result.warnings:
        log.warning(w)
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from ravn.ports.permission import CommandIntent

# ---------------------------------------------------------------------------
# Stage result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageAllow:
    """Stage result: proceed normally."""


@dataclass(frozen=True)
class StageDeny:
    """Stage result: command is blocked."""

    reason: str


@dataclass(frozen=True)
class StageWarn:
    """Stage result: proceed but surface a warning to the caller."""

    message: str


StageResult = StageAllow | StageDeny | StageWarn


# ---------------------------------------------------------------------------
# Pipeline result
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Outcome of running the full validation pipeline."""

    allowed: bool
    deny_reason: str | None
    warnings: list[str]
    intent: CommandIntent


# ---------------------------------------------------------------------------
# Shared constants — import these in permission_enforcer.py; never redefine
# ---------------------------------------------------------------------------

# Commands allowed in READ_ONLY mode (first token after sudo-unwrapping).
# Kept in sync with permission_enforcer.BashValidator's whitelist logic.
_READ_ONLY_WHITELIST: frozenset[str] = frozenset(
    {
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ripgrep",
        "ls",
        "ll",
        "la",
        "dir",
        "find",
        "locate",
        "which",
        "type",
        "file",
        "stat",
        "wc",
        "sort",
        "uniq",
        "diff",
        "comm",
        "echo",
        "printf",
        "pwd",
        "date",
        "whoami",
        "id",
        "env",
        "printenv",
        "uname",
        "hostname",
        "uptime",
        "ps",
        "top",
        "htop",
        "df",
        "du",
        "free",
        "lsof",
        "netstat",
        "ss",
        "ifconfig",
        "ip",
        # git — subcommand is checked separately
        "git",
    }
)

# Git subcommands that are safe to run in READ_ONLY mode.
# NOTE: ``stash`` is intentionally absent — git stash modifies the stash
# ref and the working tree, so it is a write operation.
_GIT_READ_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "branch",
        "tag",
        "remote",
        "fetch",
        "ls-files",
        "ls-tree",
        "cat-file",
        "rev-parse",
        "rev-list",
        "describe",
        "shortlog",
        "blame",
        "help",
        "version",
    }
)

# Git subcommands that mutate state (write operations).
_GIT_WRITE_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "add",
        "commit",
        "push",
        "pull",
        "merge",
        "rebase",
        "reset",
        "restore",
        "checkout",
        "switch",
        "apply",
        "am",
        "cherry-pick",
        "revert",
        "clean",
        "init",
        "clone",
        "stash",
        "config",
        "submodule",
    }
)

# Patterns that are ALWAYS blocked regardless of permission mode.
# Each entry is (compiled_pattern, human_readable_label).
# permission_enforcer.py iterates these via: ``for pattern, label in _ALWAYS_BLOCKED``
_ALWAYS_BLOCKED: list[tuple[re.Pattern[str], str]] = [
    # Recursive rm of root or critical paths
    (re.compile(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*\s+/\s*$"), "rm -rf / (root wipe)"),
    (re.compile(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*\s+/\b"), "rm -rf on critical path"),
    # Filesystem / disk operations
    (re.compile(r"\bmkfs\b"), "mkfs (filesystem format)"),
    (re.compile(r"\bdd\s+if="), "dd if= (disk copy/wipe)"),
    (re.compile(r"\bdd\b.*\bof=/dev/(sd|hd|nvme|vd)"), "dd to block device"),
    (re.compile(r"\bchmod\s+-R\s+777\b"), "chmod -R 777 (world-writable)"),
    (re.compile(r"\bshred\b"), "shred (secure wipe)"),
    (re.compile(r"\bwipefs\b"), "wipefs (wipe filesystem signatures)"),
    (re.compile(r"\bfdisk\b"), "fdisk (partition editor)"),
    (re.compile(r"\bparted\b"), "parted (partition editor)"),
    # Fork bomb
    (re.compile(r":\s*\(\s*\)\s*\{"), "fork bomb"),
    # Kernel module management
    (re.compile(r"\binsmod\b"), "insmod (kernel module load)"),
    (re.compile(r"\brmmod\b"), "rmmod (kernel module remove)"),
    (re.compile(r"\bmodprobe\b"), "modprobe (kernel module management)"),
    # Crontab replacement
    (re.compile(r"\bcrontab\s+-r\b"), "crontab -r (crontab wipe)"),
    # Dangerous redirects to block / memory devices
    (re.compile(r">\s*/dev/(sd|hd|nvme|vd|mem|kmem)"), "redirect to block device"),
    # History tampering
    (re.compile(r"\bhistory\s+-[cw]\b"), "history clear/write"),
]

# sed in-place edit flag pattern.
_SED_INPLACE_PATTERN: re.Pattern[str] = re.compile(r"\bsed\b.*\s-[a-zA-Z]*i[a-zA-Z]*\b")

# Path traversal / home-dir patterns with human-readable labels (for warnings).
_PATH_WARN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.\./"), "path traversal: ../"),
    (re.compile(r"\$HOME\b"), "home-dir reference: $HOME"),
    (re.compile(r"\$\{?HOME\}?"), "home-dir reference: ${HOME}"),
    (re.compile(r"~(?:/|$)"), "home-dir reference: ~/"),
]

# Same patterns without labels — for use in BashValidator (Deny, not Warn).
_PATH_TRAVERSAL_PATTERNS: list[re.Pattern[str]] = [pat for pat, _ in _PATH_WARN_PATTERNS]

__all__ = ["_PATH_TRAVERSAL_PATTERNS"]

# Network-access commands.
_NETWORK_COMMANDS: frozenset[str] = frozenset(
    {
        "curl",
        "wget",
        "ssh",
        "scp",
        "sftp",
        "rsync",
        "nc",
        "netcat",
        "ncat",
        "ftp",
        "telnet",
        "ping",
        "traceroute",
        "dig",
        "nslookup",
        "host",
    }
)

# Process-management commands (for intent classification only).
_PROCESS_MGMT_COMMANDS: frozenset[str] = frozenset(
    {
        "kill",
        "killall",
        "pkill",
        "pgrep",
        "ps",
        "top",
        "htop",
        "jobs",
        "bg",
        "fg",
        "wait",
        "nohup",
        "nice",
        "renice",
        "timeout",
        "watch",
        "strace",
        "ltrace",
        "gdb",
    }
)

# Package-management commands.
_PACKAGE_MGMT_COMMANDS: frozenset[str] = frozenset(
    {
        "apt",
        "apt-get",
        "dpkg",
        "yum",
        "dnf",
        "rpm",
        "zypper",
        "pacman",
        "brew",
        "pip",
        "pip3",
        "pip3.12",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "gem",
        "cargo",
        "go",
        "conda",
        "mamba",
        "poetry",
        "uv",
    }
)

# System-administration commands.
_SYSTEM_ADMIN_COMMANDS: frozenset[str] = frozenset(
    {
        "systemctl",
        "service",
        "mount",
        "umount",
        "chown",
        "chmod",
        "chgrp",
        "useradd",
        "userdel",
        "usermod",
        "groupadd",
        "groupdel",
        "passwd",
        "visudo",
        "iptables",
        "ufw",
        "firewall-cmd",
        "setenforce",
        "aa-enforce",
        "sysctl",
        "ulimit",
        "swapoff",
        "swapon",
        "reboot",
        "shutdown",
        "halt",
        "poweroff",
        "init",
    }
)

# Write-intent commands (not destructive).
_WRITE_COMMANDS: frozenset[str] = frozenset(
    {
        "cp",
        "mv",
        "mkdir",
        "touch",
        "tee",
        "install",
        "ln",
        "truncate",
        "sed",
        "awk",
        "perl",
        "python",
        "python3",
        "python3.12",
        "ruby",
        "node",
        "bash",
        "sh",
        "zsh",
        "fish",
        "ksh",
        "make",
        "cmake",
    }
)

# Destructive (but not always-blocked) commands.
_DESTRUCTIVE_COMMANDS: frozenset[str] = frozenset({"rm", "rmdir"})

# System paths that trigger warnings in WORKSPACE_WRITE mode.
_SYSTEM_PATH_WARN_PREFIXES: tuple[str, ...] = (
    "/etc",
    "/usr",
    "/var",
    "/boot",
    "/sys",
    "/proc",
)


# ---------------------------------------------------------------------------
# Module-level sudo-unwrapping helper (importable by permission_enforcer.py)
# ---------------------------------------------------------------------------


def unwrap_sudo(command: str) -> str:
    """Strip leading ``sudo [flags]`` tokens recursively.

    Examples::

        unwrap_sudo("sudo rm foo")         # → "rm foo"
        unwrap_sudo("sudo -u root rm foo") # → "rm foo"
        unwrap_sudo("sudo sudo grep x y")  # → "grep x y"
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return command

    while tokens and tokens[0] == "sudo":
        tokens = tokens[1:]
        while tokens and tokens[0].startswith("-"):
            if tokens[0] in ("-u", "-g", "-H", "-R", "-T"):
                tokens = tokens[2:] if len(tokens) > 1 else []
            else:
                tokens = tokens[1:]

    return shlex.join(tokens) if tokens else command


# ---------------------------------------------------------------------------
# BashValidationPipeline
# ---------------------------------------------------------------------------


class BashValidationPipeline:
    """Five-stage bash command validation pipeline.

    Instantiate once and call :meth:`validate` for each command::

        pipeline = BashValidationPipeline()
        result = pipeline.validate("ls .", mode="read_only")
    """

    def validate(
        self,
        command: str,
        mode: str,
        workspace_root: Path | None = None,
    ) -> PipelineResult:
        """Run all validation stages and return a :class:`PipelineResult`.

        Args:
            command:        Shell command string to validate.
            mode:           Permission mode (``"read_only"``,
                            ``"workspace_write"``, ``"full_access"``,
                            ``"prompt"``).
            workspace_root: Optional root used for Stage 4 boundary checks.
                            Defaults to CWD when *None*.
        """
        warnings: list[str] = []

        # Stage 3 (destructive) runs first: unconditional, mode-independent.
        s3 = self._stage3_destructive(command)
        if isinstance(s3, StageDeny):
            return PipelineResult(
                allowed=False,
                deny_reason=s3.reason,
                warnings=warnings,
                intent=CommandIntent.DESTRUCTIVE,
            )

        # Stage 1 — mode validation (sudo unwrapping applied inside)
        s1 = self._stage1_mode(command, mode)
        if isinstance(s1, StageDeny):
            return PipelineResult(
                allowed=False,
                deny_reason=s1.reason,
                warnings=warnings,
                intent=CommandIntent.UNKNOWN,
            )
        if isinstance(s1, StageWarn):
            warnings.append(s1.message)

        # Stage 2 — sed in-place editing
        s2 = self._stage2_sed(command, mode)
        if isinstance(s2, StageDeny):
            return PipelineResult(
                allowed=False,
                deny_reason=s2.reason,
                warnings=warnings,
                intent=CommandIntent.UNKNOWN,
            )
        if isinstance(s2, StageWarn):
            warnings.append(s2.message)

        # Stage 4 — path validation (warnings only, never blocks)
        warnings.extend(self._stage4_paths(command, workspace_root))

        # Stage 5 — intent classification
        intent = self._stage5_classify(command)

        return PipelineResult(
            allowed=True,
            deny_reason=None,
            warnings=warnings,
            intent=intent,
        )

    # ------------------------------------------------------------------
    # Stage 1 — mode validation
    # ------------------------------------------------------------------

    def _stage1_mode(self, command: str, mode: str) -> StageResult:
        inner = unwrap_sudo(command)

        if mode in ("full_access", "allow_all"):
            return StageAllow()

        if mode == "deny_all":
            return StageDeny("mode=deny_all blocks all commands")

        if mode == "prompt":
            return StageWarn(f"command requires interactive confirmation: {inner!r}")

        if mode == "read_only":
            return self._check_read_only_whitelist(inner)

        if mode == "workspace_write":
            return self._check_workspace_write(command)

        return StageDeny(f"unknown permission mode: {mode!r}")

    def _check_read_only_whitelist(self, inner: str) -> StageResult:
        """Return Allow if *inner* is a whitelisted read-only command."""
        try:
            tokens = shlex.split(inner)
        except ValueError:
            return StageDeny(f"cannot parse command: {inner!r}")

        if not tokens:
            return StageAllow()

        cmd = tokens[0]
        if cmd not in _READ_ONLY_WHITELIST:
            return StageDeny(f"command {cmd!r} is not in the read_only whitelist")

        if cmd == "git":
            subcmd = tokens[1] if len(tokens) > 1 else ""
            if subcmd not in _GIT_READ_SUBCOMMANDS:
                return StageDeny(f"git subcommand {subcmd!r} is not allowed in read_only mode")

        return StageAllow()

    def _check_workspace_write(self, command: str) -> StageResult:
        """Warn if command references known system paths."""
        for prefix in _SYSTEM_PATH_WARN_PREFIXES:
            if re.search(rf"(?:^|\s|['\"]){re.escape(prefix)}(?:/|\s|['\"]|$)", command):
                return StageWarn(
                    f"command references system path {prefix!r} — "
                    "proceed with caution in workspace_write mode"
                )
        return StageAllow()

    # ------------------------------------------------------------------
    # Stage 2 — sed in-place validation
    # ------------------------------------------------------------------

    def _stage2_sed(self, command: str, mode: str) -> StageResult:
        if not _SED_INPLACE_PATTERN.search(command):
            return StageAllow()

        if mode == "read_only":
            return StageDeny("sed -i (in-place edit) is not permitted in read_only mode")

        return StageWarn(
            "sed -i modifies files in-place — ensure the target is within your workspace"
        )

    # ------------------------------------------------------------------
    # Stage 3 — always-blocked destructive patterns
    # ------------------------------------------------------------------

    def _stage3_destructive(self, command: str) -> StageResult:
        for pattern, label in _ALWAYS_BLOCKED:
            if pattern.search(command):
                return StageDeny(f"blocked destructive command ({label})")
        return StageAllow()

    # ------------------------------------------------------------------
    # Stage 4 — path validation (warnings only)
    # ------------------------------------------------------------------

    def _stage4_paths(self, command: str, workspace_root: Path | None) -> list[str]:
        """Return warning strings for suspicious path references."""
        result: list[str] = []

        for pattern, label in _PATH_WARN_PATTERNS:
            if pattern.search(command):
                result.append(f"path warning: {label}")

        if workspace_root is not None:
            ws_root_str = str(workspace_root.resolve())
            for match in re.finditer(r"(?:^|\s|['\"])(/[^\s'\"]+)", command):
                abs_path = match.group(1).rstrip("/")
                if not abs_path.startswith(ws_root_str):
                    result.append(
                        f"path warning: absolute path {abs_path!r} "
                        f"is outside workspace {ws_root_str!r}"
                    )
                    break  # one warning per command is enough

        return result

    # ------------------------------------------------------------------
    # Stage 5 — intent classification
    # ------------------------------------------------------------------

    def _stage5_classify(self, command: str) -> CommandIntent:
        """Classify the primary intent of *command* across pipeline segments."""
        parts = re.split(r"\|{1,2}|&&|;", command)
        intents = [self._classify_single(p.strip()) for p in parts if p.strip()]
        return self._highest_intent(intents)

    def _classify_single(self, command: str) -> CommandIntent:
        inner = unwrap_sudo(command)
        try:
            tokens = shlex.split(inner)
        except ValueError:
            return CommandIntent.UNKNOWN

        if not tokens:
            return CommandIntent.READ_ONLY

        cmd = tokens[0]

        if cmd == "sudo":
            return CommandIntent.SYSTEM_ADMIN

        if cmd in _DESTRUCTIVE_COMMANDS:
            return CommandIntent.DESTRUCTIVE

        if cmd in _SYSTEM_ADMIN_COMMANDS:
            return CommandIntent.SYSTEM_ADMIN

        if cmd in _PACKAGE_MGMT_COMMANDS:
            return CommandIntent.PACKAGE_MANAGEMENT

        if cmd in _NETWORK_COMMANDS:
            return CommandIntent.NETWORK

        if cmd in _PROCESS_MGMT_COMMANDS:
            return CommandIntent.PROCESS_MANAGEMENT

        if cmd == "git":
            subcmd = tokens[1] if len(tokens) > 1 else ""
            if subcmd in _GIT_WRITE_SUBCOMMANDS:
                return CommandIntent.WRITE
            return CommandIntent.READ_ONLY

        if cmd in _WRITE_COMMANDS:
            return CommandIntent.WRITE

        if cmd in _READ_ONLY_WHITELIST:
            return CommandIntent.READ_ONLY

        return CommandIntent.UNKNOWN

    @staticmethod
    def _highest_intent(intents: list[CommandIntent]) -> CommandIntent:
        """Return the highest-risk intent from *intents*."""
        priority = [
            CommandIntent.DESTRUCTIVE,
            CommandIntent.SYSTEM_ADMIN,
            CommandIntent.PACKAGE_MANAGEMENT,
            CommandIntent.NETWORK,
            CommandIntent.PROCESS_MANAGEMENT,
            CommandIntent.WRITE,
            CommandIntent.UNKNOWN,
            CommandIntent.READ_ONLY,
        ]
        for intent in priority:
            if intent in intents:
                return intent
        return CommandIntent.READ_ONLY
