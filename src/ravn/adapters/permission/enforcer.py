"""Full permission enforcement engine for Ravn.

The PermissionEnforcer is the security backbone for Ravn tool execution.

Architecture
------------
- BashValidator   — multi-stage bash command analysis pipeline
- PermissionEnforcer — orchestrates all checks; implements both
  PermissionEnforcerPort (rich API) and PermissionPort (backward-compat)

Modes
-----
- read_only       — no state mutation; bash writes/network denied
- workspace_write — writes allowed within workspace root only
- full_access     — unrestricted (explicit opt-in required)
- prompt          — interactive confirmation for every non-trivially safe action

Rule evaluation order
---------------------
1. Explicit ``deny`` list  → Deny immediately
2. Explicit ``allow`` list → Allow immediately
3. Explicit ``ask``  list  → NeedsApproval immediately
4. Ordered ``rules``       → First match wins
5. Mode default            → Fallback based on mode + command/path analysis
"""

from __future__ import annotations

import fnmatch
import logging
import re
import shlex
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from ravn.adapters.memory.approval import ApprovalMemory
from ravn.adapters.permission.bash_validator import (
    _ALWAYS_BLOCKED,
    _GIT_WRITE_SUBCOMMANDS,
    _NETWORK_COMMANDS,
    _PACKAGE_MGMT_COMMANDS,
    _PATH_TRAVERSAL_PATTERNS,
    _READ_ONLY_WHITELIST,
    _SED_INPLACE_PATTERN,
    _SYSTEM_ADMIN_COMMANDS,
    _WRITE_COMMANDS,
    unwrap_sudo,
)
from ravn.adapters.tools.file_security import (
    _SYSTEM_PREFIXES,
    DEFAULT_BINARY_CHECK_BYTES,
    PathSecurityError,
    is_binary,
    resolve_safe,
)
from ravn.config import PermissionConfig
from ravn.ports.permission import (
    Allow,
    CommandIntent,
    Deny,
    NeedsApproval,
    PermissionDecision,
    PermissionEnforcerPort,
    PermissionPort,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants local to permission_enforcer (not shared with bash_validator)
# ---------------------------------------------------------------------------

# System paths that tools should never touch (broader than the warn-only list
# in bash_validator._SYSTEM_PATH_WARN_PREFIXES — these trigger hard Deny).
_SYSTEM_PATH_PREFIXES: tuple[str, ...] = _SYSTEM_PREFIXES + (
    "/dev",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/root",
)

# Tool names that correspond to bash/shell execution.
_BASH_TOOL_NAMES: frozenset[str] = frozenset({"bash", "shell", "run_command", "execute"})

# Tool names that correspond to file write operations.
_FILE_WRITE_TOOL_NAMES: frozenset[str] = frozenset(
    {"write_file", "edit_file", "create_file", "delete_file", "move_file"}
)

# Tool names that correspond to file read operations.
_FILE_READ_TOOL_NAMES: frozenset[str] = frozenset(
    {"read_file", "view_file", "grep_search", "list_directory", "file_search"}
)


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


@dataclass
class PermissionAuditEntry:
    """One structured audit record for a permission decision."""

    tool: str
    args_redacted: dict
    mode: str
    decision: str  # "allow", "deny", "needs_approval"
    reason: str
    timestamp: float = field(default_factory=time.time)


_DEFAULT_MAX_AUDIT_ENTRIES = 10_000

# Keys whose values are redacted in audit entries.
_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {"token", "api_key", "password", "secret", "key", "auth", "credential"}
)


def _redact_args(args: dict) -> dict:
    """Return a copy of *args* with sensitive values replaced by '[REDACTED]'.

    Recurses into nested dicts so that keys like ``{"config": {"api_key": "…"}}``
    are also redacted.
    """
    result: dict = {}
    for k, v in args.items():
        if any(s in k.lower() for s in _SENSITIVE_KEYS):
            result[k] = "[REDACTED]"
        elif isinstance(v, dict):
            result[k] = _redact_args(v)
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# BashValidator — multi-stage command analysis
# ---------------------------------------------------------------------------


class BashValidator:
    """Validates a bash command through a multi-stage security pipeline.

    Pipeline stages
    ---------------
    1. Destructive pattern check   — always-blocked list (immediate Deny)
    2. Recursive sudo unwrapping   — follow sudo chains to the inner command
    3. Sed in-place edit check     — deny ``sed -i`` mutations
    4. Path traversal detection    — deny ``../``, ``$HOME``, system paths
    5. Command intent classification — determines the CommandIntent
    """

    def classify(self, command: str) -> CommandIntent:
        """Classify the *primary* intent of a shell command.

        Unwraps sudo and pipeline chains to find the operative command, then
        returns the highest-risk intent among all commands in the pipeline.
        """
        commands = self._split_pipeline(command)
        intents = [self._classify_single(cmd) for cmd in commands]
        return self._highest_intent(intents)

    def validate(self, command: str, mode: str) -> PermissionDecision:
        """Full pipeline validation for *command* in the given *mode*.

        Returns Allow, Deny, or NeedsApproval.
        """
        # Stage 1: always-blocked destructive patterns
        for pattern, label in _ALWAYS_BLOCKED:
            if pattern.search(command):
                return Deny(f"command matches always-blocked pattern ({label})")

        # Stage 2: unwrap sudo recursively to find inner command
        inner = unwrap_sudo(command)

        # Stage 3: sed in-place editing
        if _SED_INPLACE_PATTERN.search(command):
            if mode in ("read_only",):
                return Deny("sed -i (in-place edit) not permitted in read_only mode")
            if mode == "workspace_write":
                return NeedsApproval("Allow in-place sed edit? This modifies files directly.")

        # Stage 4: path traversal
        traversal = self._check_path_traversal(command)
        if traversal is not None:
            return traversal

        # Stage 5: classify intent and apply mode policy
        intent = self.classify(inner)
        return self._apply_mode_policy(intent, mode, inner)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_pipeline(self, command: str) -> list[str]:
        """Split a shell pipeline / compound command into individual commands."""
        # Split on |, &&, ||, ; to get individual commands
        parts = re.split(r"\|{1,2}|&&|;", command)
        return [p.strip() for p in parts if p.strip()]

    def _classify_single(self, command: str) -> CommandIntent:
        """Classify a single (non-piped) command."""
        try:
            tokens = shlex.split(command)
        except ValueError:
            return CommandIntent.UNKNOWN

        if not tokens:
            return CommandIntent.READ_ONLY

        cmd = tokens[0]

        # sudo always elevates to system_admin
        if cmd == "sudo":
            return CommandIntent.SYSTEM_ADMIN

        if cmd in _SYSTEM_ADMIN_COMMANDS:
            return CommandIntent.SYSTEM_ADMIN

        if cmd in _PACKAGE_MGMT_COMMANDS:
            return CommandIntent.PACKAGE_MANAGEMENT

        if cmd in _NETWORK_COMMANDS:
            return CommandIntent.NETWORK

        if cmd == "git":
            subcmd = tokens[1] if len(tokens) > 1 else ""
            if subcmd in _GIT_WRITE_SUBCOMMANDS:
                return CommandIntent.WRITE
            return CommandIntent.READ_ONLY

        if cmd in ("rm", "rmdir"):
            return CommandIntent.DESTRUCTIVE

        if cmd in _WRITE_COMMANDS:
            return CommandIntent.WRITE

        if cmd in _READ_ONLY_WHITELIST:
            return CommandIntent.READ_ONLY

        # Unknown commands are treated as WRITE-level for safety: they may
        # mutate state even though we cannot determine the exact intent.
        return CommandIntent.WRITE

    def _highest_intent(self, intents: list[CommandIntent]) -> CommandIntent:
        """Return the highest-risk intent from a list."""
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

    def _check_path_traversal(self, command: str) -> Deny | None:
        """Return Deny if the command contains path traversal sequences."""
        for pattern in _PATH_TRAVERSAL_PATTERNS:
            if pattern.search(command):
                return Deny(f"command contains path traversal: {pattern.pattern!r}")

        # Check for explicit system path access
        for prefix in _SYSTEM_PATH_PREFIXES:
            # Match the prefix as a standalone argument (not mid-word)
            if re.search(rf"(?:^|\s|['\"]){re.escape(prefix)}(?:/|\s|['\"]|$)", command):
                return Deny(f"command references system path: {prefix!r}")

        return None

    def _apply_mode_policy(
        self, intent: CommandIntent, mode: str, command: str
    ) -> PermissionDecision:
        """Translate intent + mode into a PermissionDecision."""
        if intent == CommandIntent.DESTRUCTIVE:
            return Deny(f"destructive command not permitted (intent={intent})")

        if mode in ("full_access", "allow_all"):
            if intent == CommandIntent.SYSTEM_ADMIN:
                return NeedsApproval(
                    f"This command requires system administration: {command!r}. Allow?"
                )
            return Allow()

        if mode == "read_only":
            if intent != CommandIntent.READ_ONLY:
                return Deny(f"mode=read_only does not permit {intent} commands")
            return Allow()

        if mode == "workspace_write":
            if intent == CommandIntent.READ_ONLY:
                return Allow()
            if intent == CommandIntent.WRITE:
                return Allow()
            if intent == CommandIntent.NETWORK:
                return NeedsApproval(f"Allow network command in workspace_write mode: {command!r}?")
            if intent == CommandIntent.PACKAGE_MANAGEMENT:
                return NeedsApproval(
                    f"Allow package management command in workspace_write mode: {command!r}?"
                )
            if intent == CommandIntent.SYSTEM_ADMIN:
                return Deny("system administration commands not permitted in workspace_write mode")
            return Deny(f"command intent {intent!r} not permitted in workspace_write mode")

        if mode == "prompt":
            return NeedsApproval(f"Allow command: {command!r}?")

        if mode == "deny_all":
            return Deny("mode=deny_all blocks all commands")

        return Deny(f"unknown mode {mode!r}")


# ---------------------------------------------------------------------------
# PermissionEnforcer — main orchestrator
# ---------------------------------------------------------------------------


class PermissionEnforcer(PermissionEnforcerPort, PermissionPort):
    """Full permission enforcement engine driven by PermissionConfig.

    Implements both PermissionEnforcerPort (rich API) and PermissionPort
    (backward-compatible simple API) so it can be dropped in wherever either
    interface is expected.

    Decision pipeline
    -----------------
    1. Explicit deny  list — Deny  immediately
    2. Explicit allow list — Allow immediately
    3. Explicit ask   list — NeedsApproval immediately
    4. Ordered rules       — First matching rule wins
    5. Mode default        — Falls through to mode + tool-specific analysis
    """

    def __init__(
        self,
        config: PermissionConfig,
        workspace_root: Path | None = None,
        max_audit_entries: int = _DEFAULT_MAX_AUDIT_ENTRIES,
        approval_memory: ApprovalMemory | None = None,
    ) -> None:
        self._config = config
        self._mode = config.mode
        self._workspace_root: Path = workspace_root or (
            Path(config.workspace_root).resolve() if config.workspace_root else Path.cwd()
        )
        self._bash_validator = BashValidator()
        self._audit: deque[PermissionAuditEntry] = deque(maxlen=max_audit_entries)
        self._approval_memory: ApprovalMemory | None = approval_memory

    @property
    def audit_log(self) -> list[PermissionAuditEntry]:
        """Read-only view of the audit log (most-recent last)."""
        return list(self._audit)

    # ------------------------------------------------------------------
    # PermissionEnforcerPort implementation
    # ------------------------------------------------------------------

    async def evaluate(self, tool_name: str, args: dict) -> PermissionDecision:
        """Evaluate tool_name + args against the full enforcement pipeline."""
        # Step 1: explicit lists take precedence
        decision = self._check_explicit_lists(tool_name)
        if decision is not None:
            self._record_audit(tool_name, args, decision)
            return decision

        # Step 2: ordered rules
        decision = self._check_rules(tool_name)
        if decision is not None:
            self._record_audit(tool_name, args, decision)
            return decision

        # Step 3: mode default + tool-specific analysis
        decision = self._apply_mode_default(tool_name, args)
        self._record_audit(tool_name, args, decision)
        return decision

    def check_file_write(self, path: str | Path, workspace_root: Path) -> PermissionDecision:
        """Validate a file write target against workspace and system boundaries."""
        try:
            resolved = resolve_safe(path, workspace_root)
        except PathSecurityError as exc:
            return Deny(str(exc))

        # Binary detection for existing files
        p = Path(resolved)
        if p.exists() and p.is_file():
            with suppress(OSError):
                sample = p.read_bytes()[: self._config_binary_check_bytes()]
                if is_binary(sample):
                    return Deny(f"refusing to write binary file: {resolved}")

        return Allow()

    def check_bash(self, command: str) -> PermissionDecision:
        """Validate a bash command through the multi-stage pipeline."""
        return self._bash_validator.validate(command, self._mode)

    def record_approval(self, tool_name: str, args: dict) -> None:
        """Persist an explicit user approval so the same command is auto-approved later.

        Only bash tool calls are recorded; all other tool types are ignored.
        This method is idempotent — calling it multiple times for the same
        command leaves exactly one entry in the approval memory.
        """
        if self._approval_memory is None:
            return
        if tool_name not in _BASH_TOOL_NAMES:
            return
        command = args.get("command", "")
        if command:
            self._approval_memory.remember(command)

    # ------------------------------------------------------------------
    # PermissionPort implementation (backward-compat)
    # ------------------------------------------------------------------

    async def check(self, permission: str) -> bool:
        """Simple permission check — evaluates against explicit lists and rules.

        Used by PermissionHook for string-based permission queries.
        """
        decision = self._check_explicit_lists(permission)
        if decision is None:
            decision = self._check_rules(permission)
        if decision is None:
            decision = self._mode_default_for_permission(permission)
        return isinstance(decision, Allow)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_explicit_lists(self, name: str) -> PermissionDecision | None:
        """Check deny/allow/ask explicit lists; return None if not matched."""
        if self._matches_any(name, self._config.deny):
            return Deny(f"tool/permission {name!r} is in the explicit deny list")
        if self._matches_any(name, self._config.allow):
            return Allow()
        if self._matches_any(name, self._config.ask):
            return NeedsApproval(f"Allow {name!r}?")
        return None

    def _check_rules(self, name: str) -> PermissionDecision | None:
        """Evaluate ordered rules; return first match or None."""
        for rule in self._config.rules:
            if fnmatch.fnmatch(name, rule.pattern):
                if rule.action == "allow":
                    return Allow()
                if rule.action == "deny":
                    return Deny(f"rule pattern {rule.pattern!r} denies {name!r}")
                return NeedsApproval(f"Rule requires approval for {name!r}. Allow?")
        return None

    def _apply_mode_default(self, tool_name: str, args: dict) -> PermissionDecision:
        """Apply mode-based defaults with tool-specific analysis.

        Bash tools and file-write tools are always inspected in depth before
        the mode default is applied — this ensures the always-blocked list and
        workspace boundaries are enforced even in full_access mode.
        """
        # Bash tools — always validated through the full pipeline regardless of mode.
        # The always-blocked patterns must fire even in full_access.
        if tool_name in _BASH_TOOL_NAMES:
            command = args.get("command", "")
            if not command:
                return Deny("bash tool called with no command")
            decision = self.check_bash(command)
            # In prompt mode, auto-approve previously approved commands.
            # Only NeedsApproval results in prompt mode are eligible — Deny is
            # never overridden, and other modes (workspace_write, etc.) must
            # retain their own NeedsApproval gates (e.g. network commands).
            if (
                isinstance(decision, NeedsApproval)
                and self._mode == "prompt"
                and self._approval_memory is not None
                and self._approval_memory.is_approved(command)
            ):
                self._approval_memory.record_auto_approval(command)
                return Allow()
            return decision

        # File write tools — validate boundaries before applying mode defaults.
        if tool_name in _FILE_WRITE_TOOL_NAMES:
            path = args.get("path", "") or args.get("file_path", "")
            if not path:
                return self._mode_allow_or_deny(tool_name)
            return self._file_write_for_mode(path)

        # File read tools — allowed unless mode explicitly blocks everything.
        if tool_name in _FILE_READ_TOOL_NAMES and self._mode not in ("deny_all",):
            return Allow()

        # Mode-specific defaults for all other tools.
        return self._mode_allow_or_deny(tool_name)

    def _mode_allow_or_deny(self, tool_name: str) -> PermissionDecision:
        """Return the mode-level default decision for a non-bash, non-file tool."""
        if self._mode in ("full_access", "allow_all"):
            return Allow()

        if self._mode == "deny_all":
            return Deny("mode=deny_all blocks all tool calls")

        if self._mode == "prompt":
            return NeedsApproval(f"Allow tool {tool_name!r}?")

        if self._mode == "read_only":
            return Deny(f"mode=read_only does not permit tool {tool_name!r}")

        if self._mode == "workspace_write":
            return Allow()

        return Deny(f"no rule matched for {tool_name!r} in mode {self._mode!r}")

    def _file_write_for_mode(self, path: str) -> PermissionDecision:
        """Validate file write based on mode."""
        if self._mode == "read_only":
            return Deny("mode=read_only does not permit file writes")
        if self._mode == "deny_all":
            return Deny("mode=deny_all blocks all tool calls")
        if self._mode == "prompt":
            return NeedsApproval(f"Allow file write to {path!r}?")
        if self._mode == "workspace_write":
            return self.check_file_write(path, self._workspace_root)
        # full_access / allow_all
        return Allow()

    def _mode_default_for_permission(self, permission: str) -> PermissionDecision:
        """Fallback mode default for string-based permission checks."""
        if self._mode in ("full_access", "allow_all"):
            return Allow()
        if self._mode == "deny_all":
            return Deny("mode=deny_all")
        if self._mode == "prompt":
            return NeedsApproval(f"Allow {permission!r}?")
        if self._mode == "read_only":
            # Read permissions are allowed; write/exec are not
            if any(w in permission for w in ("write", "delete", "execute", "bash", "shell")):
                return Deny(f"mode=read_only denies {permission!r}")
            return Allow()
        # workspace_write — allow by default
        return Allow()

    @staticmethod
    def _matches_any(name: str, patterns: list[str]) -> bool:
        """Return True if *name* matches any glob pattern in *patterns*."""
        return any(fnmatch.fnmatch(name, p) for p in patterns)

    def _config_binary_check_bytes(self) -> int:
        """Return the binary-check byte count from the shared file_security constant."""
        return DEFAULT_BINARY_CHECK_BYTES

    def _record_audit(self, tool: str, args: dict, decision: PermissionDecision) -> None:
        """Append a decision to the bounded audit log."""
        if isinstance(decision, Allow):
            dec_str, reason = "allow", ""
        elif isinstance(decision, Deny):
            dec_str, reason = "deny", decision.reason
        else:
            dec_str, reason = "needs_approval", decision.question

        entry = PermissionAuditEntry(
            tool=tool,
            args_redacted=_redact_args(args),
            mode=self._mode,
            decision=dec_str,
            reason=reason,
        )
        self._audit.append(entry)
        logger.debug(
            "permission tool=%r decision=%s mode=%s reason=%s",
            tool,
            dec_str,
            self._mode,
            reason or "(none)",
        )
