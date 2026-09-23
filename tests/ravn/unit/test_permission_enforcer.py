"""Unit tests for the permission enforcement engine (NIU-429)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ravn.adapters.permission.bash_validator import unwrap_sudo
from ravn.adapters.permission.enforcer import (
    BashValidator,
    PermissionEnforcer,
    _redact_args,
)
from ravn.adapters.tools.hooks import EnforcerHook
from ravn.config import PermissionConfig, PermissionRuleConfig
from ravn.domain.exceptions import PermissionDeniedError
from ravn.ports.permission import (
    Allow,
    CommandIntent,
    Deny,
    NeedsApproval,
    PermissionDecision,
    PermissionEnforcerPort,
    PermissionMode,
    PermissionPort,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(mode: str = "workspace_write", **kwargs) -> PermissionConfig:
    return PermissionConfig(mode=mode, **kwargs)


def _enforcer(
    mode: str = "workspace_write",
    workspace_root: Path | None = None,
    **kwargs,
) -> PermissionEnforcer:
    cfg = _cfg(mode=mode, **kwargs)
    return PermissionEnforcer(cfg, workspace_root=workspace_root or Path("/workspace"))


# ---------------------------------------------------------------------------
# PermissionMode enum
# ---------------------------------------------------------------------------


class TestPermissionMode:
    def test_new_modes_exist(self) -> None:
        assert PermissionMode.READ_ONLY == "read_only"
        assert PermissionMode.WORKSPACE_WRITE == "workspace_write"
        assert PermissionMode.FULL_ACCESS == "full_access"
        assert PermissionMode.PROMPT == "prompt"

    def test_legacy_modes_still_exist(self) -> None:
        assert PermissionMode.ALLOW_ALL == "allow_all"
        assert PermissionMode.DENY_ALL == "deny_all"


# ---------------------------------------------------------------------------
# PermissionDecision types
# ---------------------------------------------------------------------------


class TestPermissionDecisionTypes:
    def test_allow_is_frozen(self) -> None:
        a = Allow()
        with pytest.raises(Exception):
            a.extra = True  # type: ignore[attr-defined]

    def test_deny_stores_reason(self) -> None:
        d = Deny(reason="too risky")
        assert d.reason == "too risky"

    def test_needs_approval_stores_question(self) -> None:
        na = NeedsApproval(question="Are you sure?")
        assert na.question == "Are you sure?"

    def test_allow_isinstance(self) -> None:
        assert isinstance(Allow(), Allow)

    def test_union_type_matching(self) -> None:
        decisions: list[PermissionDecision] = [Allow(), Deny("x"), NeedsApproval("q?")]
        kinds = [type(d).__name__ for d in decisions]
        assert kinds == ["Allow", "Deny", "NeedsApproval"]


# ---------------------------------------------------------------------------
# PermissionEnforcerPort ABC
# ---------------------------------------------------------------------------


class TestPermissionEnforcerPortABC:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            PermissionEnforcerPort()  # type: ignore[abstract]

    def test_enforcer_implements_port(self) -> None:
        e = _enforcer()
        assert isinstance(e, PermissionEnforcerPort)
        assert isinstance(e, PermissionPort)


# ---------------------------------------------------------------------------
# BashValidator — stage 1: always-blocked patterns
# ---------------------------------------------------------------------------


class TestBashValidatorAlwaysBlocked:
    @pytest.fixture
    def v(self) -> BashValidator:
        return BashValidator()

    @pytest.mark.parametrize(
        "cmd",
        [
            "mkfs.ext4 /dev/sda1",
            "dd if=/dev/zero of=/dev/sda",
            "shred -u /dev/sda",
            ":(){ :|:& };:",  # fork bomb
            "rm -rf /",
            "rm -r /boot",
        ],
    )
    def test_always_blocked_commands_denied(self, v: BashValidator, cmd: str) -> None:
        result = v.validate(cmd, "full_access")
        assert isinstance(result, Deny), f"Expected Deny for {cmd!r}, got {result}"

    def test_safe_command_not_blocked(self, v: BashValidator) -> None:
        result = v.validate("ls -la", "full_access")
        assert isinstance(result, Allow)


# ---------------------------------------------------------------------------
# BashValidator — stage 2: sudo unwrapping
# ---------------------------------------------------------------------------


class TestBashValidatorSudoUnwrap:
    @pytest.fixture
    def v(self) -> BashValidator:
        return BashValidator()

    def test_sudo_read_only_command_allowed(self, v: BashValidator) -> None:
        # sudo cat is read-only under the hood
        result = v.validate("sudo cat /etc/passwd", "full_access")
        # /etc is a system path — should be denied at path traversal stage
        assert isinstance(result, Deny)

    def test_sudo_rm_classified_as_destructive(self, v: BashValidator) -> None:
        intent = v.classify("sudo rm -rf /tmp/foo")
        # rm is destructive; sudo wraps to SYSTEM_ADMIN but rm is destructive
        assert intent in (CommandIntent.DESTRUCTIVE, CommandIntent.SYSTEM_ADMIN)

    def test_nested_sudo_unwrapped(self, v: BashValidator) -> None:
        inner = unwrap_sudo("sudo sudo ls")
        assert "sudo" not in inner.split()[0] if inner else True


# ---------------------------------------------------------------------------
# BashValidator — stage 3: sed in-place
# ---------------------------------------------------------------------------


class TestBashValidatorSedInPlace:
    @pytest.fixture
    def v(self) -> BashValidator:
        return BashValidator()

    def test_sed_inplace_denied_in_read_only(self, v: BashValidator) -> None:
        result = v.validate("sed -i 's/foo/bar/g' file.txt", "read_only")
        assert isinstance(result, Deny)

    def test_sed_inplace_needs_approval_workspace_write(self, v: BashValidator) -> None:
        result = v.validate("sed -i 's/foo/bar/g' file.txt", "workspace_write")
        assert isinstance(result, NeedsApproval)

    def test_sed_no_inplace_allowed(self, v: BashValidator) -> None:
        result = v.validate("sed 's/foo/bar/g' file.txt", "workspace_write")
        # sed without -i is just a stream filter (read + print) — Allow
        assert isinstance(result, Allow)


# ---------------------------------------------------------------------------
# BashValidator — stage 4: path traversal
# ---------------------------------------------------------------------------


class TestBashValidatorPathTraversal:
    @pytest.fixture
    def v(self) -> BashValidator:
        return BashValidator()

    @pytest.mark.parametrize(
        "cmd",
        [
            "cat ../secret.txt",
            "ls $HOME",
            "cat ${HOME}/.ssh/id_rsa",
            "ls ~/projects",
        ],
    )
    def test_traversal_patterns_denied(self, v: BashValidator, cmd: str) -> None:
        result = v.validate(cmd, "full_access")
        assert isinstance(result, Deny), f"Expected Deny for {cmd!r}, got {result}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "ls /etc/passwd",
            "cat /usr/bin/env",
            "ls /var/log",
        ],
    )
    def test_system_paths_denied(self, v: BashValidator, cmd: str) -> None:
        result = v.validate(cmd, "full_access")
        assert isinstance(result, Deny)

    def test_safe_path_allowed_full_access(self, v: BashValidator) -> None:
        result = v.validate("ls /workspace/project", "full_access")
        assert isinstance(result, Allow)


# ---------------------------------------------------------------------------
# BashValidator — stage 5: command intent classification
# ---------------------------------------------------------------------------


class TestBashValidatorIntentClassification:
    @pytest.fixture
    def v(self) -> BashValidator:
        return BashValidator()

    @pytest.mark.parametrize(
        "cmd,expected",
        [
            ("ls -la", CommandIntent.READ_ONLY),
            ("cat file.txt", CommandIntent.READ_ONLY),
            ("grep pattern file", CommandIntent.READ_ONLY),
            ("git status", CommandIntent.READ_ONLY),
            ("git log --oneline", CommandIntent.READ_ONLY),
            ("git diff HEAD", CommandIntent.READ_ONLY),
            ("git add .", CommandIntent.WRITE),
            ("git commit -m 'msg'", CommandIntent.WRITE),
            ("git push origin main", CommandIntent.WRITE),
            # stash and config are write-classified (fix 5)
            ("git stash", CommandIntent.WRITE),
            ("git config user.email test@example.com", CommandIntent.WRITE),
            ("cp src dst", CommandIntent.WRITE),
            ("mv src dst", CommandIntent.WRITE),
            ("mkdir -p /tmp/foo", CommandIntent.WRITE),
            ("rm -rf /tmp/foo", CommandIntent.DESTRUCTIVE),
            ("rm file.txt", CommandIntent.DESTRUCTIVE),
            ("curl https://example.com", CommandIntent.NETWORK),
            ("wget https://example.com", CommandIntent.NETWORK),
            ("pip install requests", CommandIntent.PACKAGE_MANAGEMENT),
            ("npm install express", CommandIntent.PACKAGE_MANAGEMENT),
            ("systemctl restart nginx", CommandIntent.SYSTEM_ADMIN),
            ("sudo chmod 777 /file", CommandIntent.SYSTEM_ADMIN),
        ],
    )
    def test_intent_classification(
        self, v: BashValidator, cmd: str, expected: CommandIntent
    ) -> None:
        intent = v.classify(cmd)
        assert intent == expected, f"cmd={cmd!r}: expected {expected}, got {intent}"

    def test_pipeline_takes_highest_intent(self, v: BashValidator) -> None:
        # ls (read) | rm (destructive) → destructive
        intent = v.classify("ls /tmp | rm /tmp/file")
        assert intent == CommandIntent.DESTRUCTIVE

    def test_unknown_command_treated_as_write(self, v: BashValidator) -> None:
        intent = v._classify_single("some_unknown_binary --flag")
        assert intent == CommandIntent.WRITE


# ---------------------------------------------------------------------------
# BashValidator — mode policies
# ---------------------------------------------------------------------------


class TestBashValidatorModePolicies:
    @pytest.fixture
    def v(self) -> BashValidator:
        return BashValidator()

    # read_only mode
    def test_read_only_allows_read_commands(self, v: BashValidator) -> None:
        assert isinstance(v.validate("ls -la", "read_only"), Allow)

    def test_read_only_denies_write_commands(self, v: BashValidator) -> None:
        assert isinstance(v.validate("cp src dst", "read_only"), Deny)

    def test_read_only_denies_network(self, v: BashValidator) -> None:
        assert isinstance(v.validate("curl https://example.com", "read_only"), Deny)

    # workspace_write mode
    def test_workspace_write_allows_read(self, v: BashValidator) -> None:
        assert isinstance(v.validate("grep pattern file.txt", "workspace_write"), Allow)

    def test_workspace_write_allows_write(self, v: BashValidator) -> None:
        assert isinstance(v.validate("cp src dst", "workspace_write"), Allow)

    def test_workspace_write_asks_for_network(self, v: BashValidator) -> None:
        result = v.validate("curl https://example.com", "workspace_write")
        assert isinstance(result, NeedsApproval)

    def test_workspace_write_asks_for_package_mgmt(self, v: BashValidator) -> None:
        result = v.validate("pip install requests", "workspace_write")
        assert isinstance(result, NeedsApproval)

    def test_workspace_write_denies_system_admin(self, v: BashValidator) -> None:
        result = v.validate("systemctl restart nginx", "workspace_write")
        assert isinstance(result, Deny)

    # full_access mode
    def test_full_access_allows_most_commands(self, v: BashValidator) -> None:
        assert isinstance(v.validate("cp src dst", "full_access"), Allow)
        assert isinstance(v.validate("npm install", "full_access"), Allow)

    def test_full_access_asks_system_admin(self, v: BashValidator) -> None:
        result = v.validate("systemctl restart nginx", "full_access")
        assert isinstance(result, NeedsApproval)

    # prompt mode
    def test_prompt_mode_asks_for_everything(self, v: BashValidator) -> None:
        result = v.validate("ls -la", "prompt")
        assert isinstance(result, NeedsApproval)


# ---------------------------------------------------------------------------
# File boundary enforcement
# ---------------------------------------------------------------------------


class TestCheckFileWrite:
    def test_allows_file_within_workspace(self, tmp_path: Path) -> None:
        e = _enforcer("workspace_write", workspace_root=tmp_path)
        result = e.check_file_write(tmp_path / "subdir" / "file.txt", tmp_path)
        assert isinstance(result, Allow)

    def test_denies_file_outside_workspace(self, tmp_path: Path) -> None:
        e = _enforcer("workspace_write", workspace_root=tmp_path)
        result = e.check_file_write("/etc/passwd", tmp_path)
        assert isinstance(result, Deny)

    def test_denies_path_traversal_escape(self, tmp_path: Path) -> None:
        e = _enforcer("workspace_write", workspace_root=tmp_path)
        result = e.check_file_write(str(tmp_path) + "/../outside.txt", tmp_path)
        assert isinstance(result, Deny)

    def test_denies_system_path(self, tmp_path: Path) -> None:
        e = _enforcer("workspace_write", workspace_root=tmp_path)
        result = e.check_file_write("/usr/local/bin/evil", tmp_path)
        assert isinstance(result, Deny)

    def test_denies_binary_file(self, tmp_path: Path) -> None:
        binary_file = tmp_path / "binary.bin"
        binary_file.write_bytes(b"\x00" * 100)
        e = _enforcer("workspace_write", workspace_root=tmp_path)
        result = e.check_file_write(binary_file, tmp_path)
        assert isinstance(result, Deny)

    def test_allows_text_file(self, tmp_path: Path) -> None:
        text_file = tmp_path / "text.txt"
        text_file.write_text("hello world")
        e = _enforcer("workspace_write", workspace_root=tmp_path)
        result = e.check_file_write(text_file, tmp_path)
        assert isinstance(result, Allow)


# ---------------------------------------------------------------------------
# Explicit allow/deny/ask lists
# ---------------------------------------------------------------------------


class TestExplicitLists:
    @pytest.mark.asyncio
    async def test_explicit_deny_blocks_tool(self) -> None:
        e = _enforcer("full_access", deny=["bash"])
        decision = await e.evaluate("bash", {"command": "ls"})
        assert isinstance(decision, Deny)

    @pytest.mark.asyncio
    async def test_explicit_allow_permits_tool(self) -> None:
        e = _enforcer("read_only", allow=["my_custom_tool"])
        decision = await e.evaluate("my_custom_tool", {})
        assert isinstance(decision, Allow)

    @pytest.mark.asyncio
    async def test_explicit_ask_requires_approval(self) -> None:
        e = _enforcer("full_access", ask=["write_file"])
        decision = await e.evaluate("write_file", {"path": "/workspace/foo.txt"})
        assert isinstance(decision, NeedsApproval)

    @pytest.mark.asyncio
    async def test_deny_takes_precedence_over_allow(self) -> None:
        e = _enforcer("full_access", deny=["bash"], allow=["bash"])
        decision = await e.evaluate("bash", {})
        assert isinstance(decision, Deny)

    @pytest.mark.asyncio
    async def test_glob_pattern_in_deny_list(self) -> None:
        e = _enforcer("full_access", deny=["git:*"])
        decision = await e.evaluate("git:write", {})
        assert isinstance(decision, Deny)

    @pytest.mark.asyncio
    async def test_glob_pattern_in_allow_list(self) -> None:
        e = _enforcer("read_only", allow=["git:*"])
        decision = await e.evaluate("git:read", {})
        assert isinstance(decision, Allow)


# ---------------------------------------------------------------------------
# Ordered rules
# ---------------------------------------------------------------------------


class TestPermissionRules:
    @pytest.mark.asyncio
    async def test_first_matching_rule_wins(self) -> None:
        rules = [
            PermissionRuleConfig(pattern="git:*", action="allow"),
            PermissionRuleConfig(pattern="git:write", action="deny"),
        ]
        e = _enforcer("read_only", rules=rules)
        # git:write matches first rule (allow) before second (deny)
        decision = await e.evaluate("git:write", {})
        assert isinstance(decision, Allow)

    @pytest.mark.asyncio
    async def test_rule_deny_action(self) -> None:
        rules = [PermissionRuleConfig(pattern="bash", action="deny")]
        e = _enforcer("full_access", rules=rules)
        decision = await e.evaluate("bash", {"command": "ls"})
        assert isinstance(decision, Deny)

    @pytest.mark.asyncio
    async def test_rule_ask_action(self) -> None:
        rules = [PermissionRuleConfig(pattern="write_file", action="ask")]
        e = _enforcer("full_access", rules=rules)
        decision = await e.evaluate("write_file", {"path": "/workspace/f.txt"})
        assert isinstance(decision, NeedsApproval)

    @pytest.mark.asyncio
    async def test_no_matching_rule_falls_through_to_mode(self) -> None:
        rules = [PermissionRuleConfig(pattern="some_other_tool", action="deny")]
        e = _enforcer("full_access", rules=rules)
        decision = await e.evaluate("bash", {"command": "ls"})
        # Falls through to full_access → Allow
        assert isinstance(decision, Allow)


# ---------------------------------------------------------------------------
# Mode-based evaluation
# ---------------------------------------------------------------------------


class TestModeEvaluation:
    @pytest.mark.asyncio
    async def test_full_access_allows_everything(self) -> None:
        e = _enforcer("full_access")
        assert isinstance(await e.evaluate("arbitrary_tool", {}), Allow)
        assert isinstance(await e.evaluate("write_file", {"path": "/workspace/f.txt"}), Allow)

    @pytest.mark.asyncio
    async def test_legacy_allow_all_allows_everything(self) -> None:
        e = _enforcer("allow_all")
        assert isinstance(await e.evaluate("bash", {"command": "ls"}), Allow)

    @pytest.mark.asyncio
    async def test_deny_all_blocks_everything(self) -> None:
        e = _enforcer("deny_all")
        decision = await e.evaluate("read_file", {"path": "/workspace/f.txt"})
        assert isinstance(decision, Deny)

    @pytest.mark.asyncio
    async def test_prompt_mode_asks_for_everything(self) -> None:
        e = _enforcer("prompt")
        decision = await e.evaluate("ls", {})
        assert isinstance(decision, NeedsApproval)

    @pytest.mark.asyncio
    async def test_read_only_allows_file_reads(self) -> None:
        e = _enforcer("read_only")
        decision = await e.evaluate("read_file", {"path": "/workspace/f.txt"})
        assert isinstance(decision, Allow)

    @pytest.mark.asyncio
    async def test_read_only_denies_file_writes(self) -> None:
        e = _enforcer("read_only")
        decision = await e.evaluate("write_file", {"path": "/workspace/f.txt"})
        assert isinstance(decision, Deny)

    @pytest.mark.asyncio
    async def test_read_only_denies_unknown_tool(self) -> None:
        e = _enforcer("read_only")
        decision = await e.evaluate("some_mutation_tool", {})
        assert isinstance(decision, Deny)

    @pytest.mark.asyncio
    async def test_workspace_write_allows_reads(self) -> None:
        e = _enforcer("workspace_write")
        decision = await e.evaluate("read_file", {"path": "/workspace/f.txt"})
        assert isinstance(decision, Allow)

    @pytest.mark.asyncio
    async def test_workspace_write_denies_write_outside_workspace(self, tmp_path: Path) -> None:
        e = _enforcer("workspace_write", workspace_root=tmp_path)
        decision = await e.evaluate("write_file", {"path": "/etc/passwd"})
        assert isinstance(decision, Deny)

    @pytest.mark.asyncio
    async def test_workspace_write_allows_write_inside_workspace(self, tmp_path: Path) -> None:
        e = _enforcer("workspace_write", workspace_root=tmp_path)
        decision = await e.evaluate("write_file", {"path": str(tmp_path / "safe.txt")})
        assert isinstance(decision, Allow)

    @pytest.mark.asyncio
    async def test_workspace_write_allows_unknown_tools(self) -> None:
        e = _enforcer("workspace_write")
        decision = await e.evaluate("custom_tool", {})
        assert isinstance(decision, Allow)


# ---------------------------------------------------------------------------
# Bash tool integration through evaluate()
# ---------------------------------------------------------------------------


class TestBashToolEvaluation:
    @pytest.mark.asyncio
    async def test_bash_tool_safe_read_allowed_workspace_write(self) -> None:
        e = _enforcer("workspace_write")
        decision = await e.evaluate("bash", {"command": "ls -la"})
        assert isinstance(decision, Allow)

    @pytest.mark.asyncio
    async def test_bash_tool_destructive_denied(self) -> None:
        e = _enforcer("full_access")
        decision = await e.evaluate("bash", {"command": "dd if=/dev/zero of=/dev/sda"})
        assert isinstance(decision, Deny)

    @pytest.mark.asyncio
    async def test_shell_tool_name_also_validated(self) -> None:
        e = _enforcer("workspace_write")
        decision = await e.evaluate("shell", {"command": "ls"})
        assert isinstance(decision, Allow)

    @pytest.mark.asyncio
    async def test_bash_tool_empty_command_denied(self) -> None:
        e = _enforcer("workspace_write")
        decision = await e.evaluate("bash", {"command": ""})
        assert isinstance(decision, Deny)

    @pytest.mark.asyncio
    async def test_bash_tool_path_traversal_denied(self) -> None:
        e = _enforcer("workspace_write")
        decision = await e.evaluate("bash", {"command": "cat ../secret"})
        assert isinstance(decision, Deny)

    @pytest.mark.asyncio
    async def test_read_only_denies_bash_write(self) -> None:
        e = _enforcer("read_only")
        decision = await e.evaluate("bash", {"command": "cp src dst"})
        assert isinstance(decision, Deny)


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail:
    @pytest.mark.asyncio
    async def test_audit_log_records_allow(self) -> None:
        e = _enforcer("full_access")
        await e.evaluate("read_file", {"path": "/workspace/f.txt"})
        assert len(e.audit_log) == 1
        entry = e.audit_log[0]
        assert entry.tool == "read_file"
        assert entry.decision == "allow"

    @pytest.mark.asyncio
    async def test_audit_log_records_deny(self) -> None:
        e = _enforcer("deny_all")
        await e.evaluate("bash", {"command": "ls"})
        assert e.audit_log[0].decision == "deny"

    @pytest.mark.asyncio
    async def test_audit_log_records_needs_approval(self) -> None:
        e = _enforcer("prompt")
        await e.evaluate("bash", {"command": "ls"})
        assert e.audit_log[0].decision == "needs_approval"

    @pytest.mark.asyncio
    async def test_audit_log_records_mode(self) -> None:
        e = _enforcer("workspace_write")
        await e.evaluate("read_file", {})
        assert e.audit_log[0].mode == "workspace_write"

    @pytest.mark.asyncio
    async def test_audit_log_redacts_sensitive_args(self) -> None:
        e = _enforcer("full_access")
        await e.evaluate("tool", {"path": "/workspace/f.txt", "token": "secret123"})
        entry = e.audit_log[0]
        assert entry.args_redacted["token"] == "[REDACTED]"
        assert entry.args_redacted["path"] == "/workspace/f.txt"

    @pytest.mark.asyncio
    async def test_audit_log_is_append_only_copy(self) -> None:
        e = _enforcer("full_access")
        await e.evaluate("tool_a", {})
        log = e.audit_log
        log.clear()
        # Internal log should not have been mutated
        assert len(e.audit_log) == 1

    @pytest.mark.asyncio
    async def test_audit_log_bounded(self) -> None:
        e = PermissionEnforcer(_cfg("full_access"), max_audit_entries=3)
        for i in range(5):
            await e.evaluate(f"tool_{i}", {})
        assert len(e.audit_log) <= 3

    @pytest.mark.asyncio
    async def test_audit_entry_has_timestamp(self) -> None:
        import time

        e = _enforcer("full_access")
        before = time.time()
        await e.evaluate("t", {})
        after = time.time()
        assert before <= e.audit_log[0].timestamp <= after

    def test_redact_args_replaces_sensitive_keys(self) -> None:
        args = {"path": "/f.txt", "api_key": "sk-abc", "token": "tok123", "data": "ok"}
        result = _redact_args(args)
        assert result["api_key"] == "[REDACTED]"
        assert result["token"] == "[REDACTED]"
        assert result["path"] == "/f.txt"
        assert result["data"] == "ok"


# ---------------------------------------------------------------------------
# PermissionPort backward-compat (check method)
# ---------------------------------------------------------------------------


class TestPermissionPortBackwardCompat:
    @pytest.mark.asyncio
    async def test_check_explicit_allow(self) -> None:
        e = _enforcer("read_only", allow=["git:read"])
        assert await e.check("git:read") is True

    @pytest.mark.asyncio
    async def test_check_explicit_deny(self) -> None:
        e = _enforcer("full_access", deny=["bash"])
        assert await e.check("bash") is False

    @pytest.mark.asyncio
    async def test_check_full_access_allows_all(self) -> None:
        e = _enforcer("full_access")
        assert await e.check("anything") is True

    @pytest.mark.asyncio
    async def test_check_deny_all_denies_all(self) -> None:
        e = _enforcer("deny_all")
        assert await e.check("anything") is False

    @pytest.mark.asyncio
    async def test_check_read_only_denies_write_permission(self) -> None:
        e = _enforcer("read_only")
        assert await e.check("write:file") is False

    @pytest.mark.asyncio
    async def test_check_read_only_allows_non_write_permission(self) -> None:
        e = _enforcer("read_only")
        assert await e.check("git:read") is True


# ---------------------------------------------------------------------------
# EnforcerHook
# ---------------------------------------------------------------------------


class TestEnforcerHook:
    @pytest.mark.asyncio
    async def test_allows_passes_args_through(self) -> None:
        hook = EnforcerHook(_enforcer("full_access"))
        args = {"command": "ls"}
        out = await hook.pre_execute("bash", args, {})
        assert out == args

    @pytest.mark.asyncio
    async def test_deny_raises_permission_denied(self) -> None:
        hook = EnforcerHook(_enforcer("deny_all"))
        with pytest.raises(PermissionDeniedError) as exc_info:
            await hook.pre_execute("bash", {"command": "ls"}, {})
        assert exc_info.value.tool_name == "bash"

    @pytest.mark.asyncio
    async def test_needs_approval_raises_permission_denied(self) -> None:
        hook = EnforcerHook(_enforcer("prompt"))
        with pytest.raises(PermissionDeniedError):
            await hook.pre_execute("bash", {"command": "ls"}, {})

    @pytest.mark.asyncio
    async def test_explicit_deny_list_blocks_tool(self) -> None:
        hook = EnforcerHook(_enforcer("full_access", deny=["dangerous_tool"]))
        with pytest.raises(PermissionDeniedError):
            await hook.pre_execute("dangerous_tool", {}, {})

    @pytest.mark.asyncio
    async def test_hook_implements_pre_tool_hook_port(self) -> None:
        from ravn.ports.hooks import PreToolHookPort

        hook = EnforcerHook(_enforcer("full_access"))
        assert isinstance(hook, PreToolHookPort)

    @pytest.mark.asyncio
    async def test_allow_does_not_raise(self) -> None:
        hook = EnforcerHook(_enforcer("workspace_write"))
        # Should not raise
        await hook.pre_execute("read_file", {"path": "/workspace/file.txt"}, {})


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestPermissionConfigIntegration:
    def test_workspace_root_config_used(self, tmp_path: Path) -> None:
        cfg = PermissionConfig(mode="workspace_write", workspace_root=str(tmp_path))
        e = PermissionEnforcer(cfg)
        assert e._workspace_root == tmp_path.resolve()

    def test_default_workspace_is_cwd(self) -> None:
        import os

        cfg = PermissionConfig(mode="workspace_write")
        e = PermissionEnforcer(cfg)
        assert e._workspace_root == Path(os.getcwd()).resolve()

    def test_explicit_workspace_root_param_overrides_config(self, tmp_path: Path) -> None:
        cfg = PermissionConfig(mode="workspace_write", workspace_root="/some/other/path")
        e = PermissionEnforcer(cfg, workspace_root=tmp_path)
        assert e._workspace_root == tmp_path


# ---------------------------------------------------------------------------
# Hyphenated / unknown modes — never read as a permissive default
# ---------------------------------------------------------------------------


class TestModeSpellingAndUnknownModes:
    async def test_hyphenated_read_only_denies_writes_and_execution(self) -> None:
        e = _enforcer("read-only")
        assert not await e.check("file:write")
        assert not await e.check("bash:execute")
        assert not await e.check("shell:execute")
        assert isinstance(await e.evaluate("write_file", {"path": "/workspace/f.txt"}), Deny)
        assert isinstance(await e.evaluate("bash", {"command": "touch f.txt"}), Deny)

    def test_unvalidated_unknown_mode_rejected_at_construction(self) -> None:
        cfg = PermissionConfig.model_construct(mode="superuser")
        with pytest.raises(ValueError, match="Unknown permission_mode 'superuser'"):
            PermissionEnforcer(cfg, workspace_root=Path("/workspace"))

    def test_bash_validator_rejects_unknown_mode(self) -> None:
        with pytest.raises(ValueError, match="Unknown permission_mode 'superuser'"):
            BashValidator().validate("ls", "superuser")

    def test_bash_validator_accepts_hyphenated_mode(self) -> None:
        assert isinstance(BashValidator().validate("touch f.txt", "read-only"), Deny)
        assert isinstance(BashValidator().validate("sed -i s/a/b/ f.txt", "read-only"), Deny)

    # A parsed mode with no policy branch must raise, not allow.  The branches
    # are unreachable through the public API, so force the private mode.

    @pytest.fixture
    def unhandled(self) -> PermissionEnforcer:
        e = _enforcer("workspace_write")
        e._mode = "future_mode"  # type: ignore[assignment]
        return e

    async def test_unhandled_mode_raises_for_permission_check(
        self, unhandled: PermissionEnforcer
    ) -> None:
        with pytest.raises(ValueError, match="no enforcement policy"):
            await unhandled.check("file:write")

    async def test_unhandled_mode_raises_for_file_write(
        self, unhandled: PermissionEnforcer
    ) -> None:
        with pytest.raises(ValueError, match="no enforcement policy"):
            await unhandled.evaluate("write_file", {"path": "/workspace/f.txt"})

    async def test_unhandled_mode_raises_for_other_tools(
        self, unhandled: PermissionEnforcer
    ) -> None:
        with pytest.raises(ValueError, match="no enforcement policy"):
            await unhandled.evaluate("web_fetch", {"url": "https://example.com"})

    def test_unhandled_mode_raises_for_bash_policy(self) -> None:
        with pytest.raises(ValueError, match="no enforcement policy"):
            BashValidator()._apply_mode_policy(
                CommandIntent.WRITE,
                "future_mode",  # type: ignore[arg-type]
                "touch f.txt",
            )


# ---------------------------------------------------------------------------
# Fix 1: _file_write_for_mode handles deny_all and prompt explicitly
# ---------------------------------------------------------------------------


class TestFileWriteForMode:
    @pytest.mark.asyncio
    async def test_deny_all_blocks_file_write_with_path(self) -> None:
        e = _enforcer("deny_all")
        decision = await e.evaluate("write_file", {"path": "/workspace/f.txt"})
        assert isinstance(decision, Deny)

    @pytest.mark.asyncio
    async def test_prompt_mode_asks_for_file_write_with_path(self) -> None:
        e = _enforcer("prompt")
        decision = await e.evaluate("write_file", {"path": "/workspace/f.txt"})
        assert isinstance(decision, NeedsApproval)

    @pytest.mark.asyncio
    async def test_full_access_allows_file_write(self) -> None:
        e = _enforcer("full_access")
        decision = await e.evaluate("write_file", {"path": "/workspace/f.txt"})
        assert isinstance(decision, Allow)

    @pytest.mark.asyncio
    async def test_allow_all_allows_file_write(self) -> None:
        e = _enforcer("allow_all")
        decision = await e.evaluate("write_file", {"path": "/workspace/f.txt"})
        assert isinstance(decision, Allow)

    def test_file_write_for_mode_deny_all_direct(self) -> None:
        e = _enforcer("deny_all")
        result = e._file_write_for_mode("/workspace/f.txt")
        assert isinstance(result, Deny)

    def test_file_write_for_mode_prompt_direct(self) -> None:
        e = _enforcer("prompt")
        result = e._file_write_for_mode("/workspace/f.txt")
        assert isinstance(result, NeedsApproval)
        assert "/workspace/f.txt" in result.question


# ---------------------------------------------------------------------------
# Fix 2: _SYSTEM_PATH_PREFIXES derived from file_security._SYSTEM_PREFIXES
# ---------------------------------------------------------------------------


class TestSystemPathPrefixDeduplication:
    def test_system_prefixes_imported_from_file_security(self) -> None:
        from ravn.adapters.permission.enforcer import _SYSTEM_PATH_PREFIXES
        from ravn.adapters.tools.file_security import _SYSTEM_PREFIXES

        # All base prefixes are included
        for prefix in _SYSTEM_PREFIXES:
            assert prefix in _SYSTEM_PATH_PREFIXES

    def test_extra_prefixes_added(self) -> None:
        from ravn.adapters.permission.enforcer import _SYSTEM_PATH_PREFIXES

        for extra in ("/dev", "/bin", "/sbin", "/lib", "/lib64", "/root"):
            assert extra in _SYSTEM_PATH_PREFIXES


# ---------------------------------------------------------------------------
# Fix 3: binary check uses DEFAULT_BINARY_CHECK_BYTES constant
# ---------------------------------------------------------------------------


class TestBinaryCheckBytes:
    def test_config_binary_check_bytes_uses_constant(self) -> None:
        from ravn.adapters.tools.file_security import DEFAULT_BINARY_CHECK_BYTES

        e = _enforcer("workspace_write")
        assert e._config_binary_check_bytes() == DEFAULT_BINARY_CHECK_BYTES


# ---------------------------------------------------------------------------
# Fix 4: audit log uses deque
# ---------------------------------------------------------------------------


class TestAuditDeque:
    def test_audit_is_a_deque(self) -> None:
        from collections import deque

        e = _enforcer("full_access")
        assert isinstance(e._audit, deque)

    def test_audit_deque_has_correct_maxlen(self) -> None:
        from collections import deque

        e = PermissionEnforcer(_cfg("full_access"), max_audit_entries=42)
        assert isinstance(e._audit, deque)
        assert e._audit.maxlen == 42

    @pytest.mark.asyncio
    async def test_audit_bounded_eviction_via_deque(self) -> None:
        e = PermissionEnforcer(_cfg("full_access"), max_audit_entries=3)
        for i in range(6):
            await e.evaluate(f"tool_{i}", {})
        log = e.audit_log
        assert len(log) == 3
        # Most-recent 3 entries survived
        assert log[-1].tool == "tool_5"
        assert log[0].tool == "tool_3"


# ---------------------------------------------------------------------------
# Fix 5: git stash and git config classified as WRITE
# ---------------------------------------------------------------------------


class TestGitStashConfigClassification:
    @pytest.fixture
    def v(self) -> BashValidator:
        return BashValidator()

    def test_git_stash_is_write(self, v: BashValidator) -> None:
        assert v.classify("git stash") == CommandIntent.WRITE

    def test_git_stash_push_is_write(self, v: BashValidator) -> None:
        assert v.classify("git stash push -m 'wip'") == CommandIntent.WRITE

    def test_git_stash_pop_is_write(self, v: BashValidator) -> None:
        assert v.classify("git stash pop") == CommandIntent.WRITE

    def test_git_config_write_is_write(self, v: BashValidator) -> None:
        assert v.classify("git config user.email foo@bar.com") == CommandIntent.WRITE

    def test_git_config_local_is_write(self, v: BashValidator) -> None:
        assert v.classify("git config --local core.autocrlf false") == CommandIntent.WRITE

    def test_git_stash_denied_in_read_only(self, v: BashValidator) -> None:
        result = v.validate("git stash", "read_only")
        assert isinstance(result, Deny)

    def test_git_config_denied_in_read_only(self, v: BashValidator) -> None:
        result = v.validate("git config user.name foo", "read_only")
        assert isinstance(result, Deny)


# ---------------------------------------------------------------------------
# Fix 6: _redact_args recurses into nested dicts
# ---------------------------------------------------------------------------


class TestRedactArgsNested:
    def test_nested_dict_sensitive_key_redacted(self) -> None:
        args = {"config": {"api_key": "sk-secret", "url": "https://example.com"}}
        result = _redact_args(args)
        assert result["config"]["api_key"] == "[REDACTED]"
        assert result["config"]["url"] == "https://example.com"

    def test_deeply_nested_redaction(self) -> None:
        args = {"outer": {"inner": {"token": "tok-abc"}}}
        result = _redact_args(args)
        assert result["outer"]["inner"]["token"] == "[REDACTED]"

    def test_non_sensitive_nested_keys_preserved(self) -> None:
        args = {"metadata": {"file": "foo.txt", "size": 42}}
        result = _redact_args(args)
        assert result["metadata"]["file"] == "foo.txt"
        assert result["metadata"]["size"] == 42

    def test_top_level_sensitive_still_redacted(self) -> None:
        args = {"token": "top-level-secret", "nested": {"password": "deep-secret"}}
        result = _redact_args(args)
        assert result["token"] == "[REDACTED]"
        assert result["nested"]["password"] == "[REDACTED]"

    def test_original_dict_not_mutated(self) -> None:
        args = {"config": {"api_key": "original"}}
        _redact_args(args)
        assert args["config"]["api_key"] == "original"
