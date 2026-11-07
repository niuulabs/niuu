"""Unit tests for the hook pipeline — ports, built-in hooks, and registry integration."""

from __future__ import annotations

import re

import pytest

from niuu.domain.tool import ToolResult
from ravn.adapters.permission.allow_deny import AllowAllPermission, DenyAllPermission
from ravn.adapters.tools.hooks import (
    _REDACTED,
    _SECRET_PATTERNS,
    AuditEntry,
    AuditHook,
    BudgetHook,
    HookPipeline,
    PermissionHook,
    SanitisationHook,
)
from ravn.domain.exceptions import PermissionDeniedError
from ravn.ports.hooks import HookPipelinePort, PostToolHookPort, PreToolHookPort
from ravn.registry import ToolRegistry
from tests.ravn.fixtures.fakes import EchoTool, FailingTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(content: str = "ok", *, is_error: bool = False) -> ToolResult:
    return ToolResult(tool_call_id="cid", content=content, is_error=is_error)


# ---------------------------------------------------------------------------
# Port ABC contracts
# ---------------------------------------------------------------------------


class TestPreToolHookPort:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            PreToolHookPort()  # type: ignore[abstract]

    def test_concrete_subclass_must_implement_pre_execute(self) -> None:
        class Incomplete(PreToolHookPort):
            pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    async def test_concrete_subclass_works(self) -> None:
        class Identity(PreToolHookPort):
            async def pre_execute(self, tool_name: str, args: dict, agent_state: dict) -> dict:
                return args

        hook = Identity()
        result = await hook.pre_execute("echo", {"msg": "hi"}, {})
        assert result == {"msg": "hi"}


class TestPostToolHookPort:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            PostToolHookPort()  # type: ignore[abstract]

    async def test_concrete_subclass_works(self) -> None:
        class Identity(PostToolHookPort):
            async def post_execute(
                self, tool_name: str, args: dict, result: ToolResult, agent_state: dict
            ) -> ToolResult:
                return result

        hook = Identity()
        r = _result("hello")
        out = await hook.post_execute("echo", {}, r, {})
        assert out is r


# ---------------------------------------------------------------------------
# HookPipelinePort
# ---------------------------------------------------------------------------


class TestHookPipelinePort:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            HookPipelinePort()  # type: ignore[abstract]

    def test_concrete_must_implement_run_pre_and_run_post(self) -> None:
        class Incomplete(HookPipelinePort):
            async def run_pre(self, tool_name: str, args: dict, state: dict) -> dict:
                return args

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    async def test_hook_pipeline_implements_port(self) -> None:
        pipeline = HookPipeline()
        assert isinstance(pipeline, HookPipelinePort)

    async def test_concrete_subclass_works(self) -> None:
        class NoopPipeline(HookPipelinePort):
            async def run_pre(self, tool_name: str, args: dict, state: dict) -> dict:
                return args

            async def run_post(
                self, tool_name: str, args: dict, result: ToolResult, state: dict
            ) -> ToolResult:
                return result

        pipeline = NoopPipeline()
        args = {"x": 1}
        out = await pipeline.run_pre("t", args, {})
        assert out == args
        r = _result("ok")
        out2 = await pipeline.run_post("t", {}, r, {})
        assert out2 is r


# ---------------------------------------------------------------------------
# PermissionHook
# ---------------------------------------------------------------------------


class TestPermissionHook:
    async def test_allows_when_permission_granted(self) -> None:
        hook = PermissionHook(AllowAllPermission())
        args = {"message": "hello"}
        result = await hook.pre_execute("echo", args, {"required_permission": "tool:echo"})
        assert result == args

    async def test_raises_when_permission_denied(self) -> None:
        hook = PermissionHook(DenyAllPermission())
        with pytest.raises(PermissionDeniedError) as exc_info:
            await hook.pre_execute("echo", {}, {"required_permission": "tool:echo"})
        assert exc_info.value.tool_name == "echo"
        assert exc_info.value.permission == "tool:echo"

    async def test_uses_empty_permission_when_not_in_state(self) -> None:
        """Missing required_permission key defaults to empty string — still allowed."""
        hook = PermissionHook(AllowAllPermission())
        args = {"x": 1}
        out = await hook.pre_execute("any_tool", args, {})
        assert out == args

    async def test_passes_args_through_unmodified(self) -> None:
        hook = PermissionHook(AllowAllPermission())
        args = {"a": 1, "b": [1, 2, 3]}
        out = await hook.pre_execute("tool", args, {"required_permission": "x"})
        assert out == args

    async def test_raises_permission_denied_error_not_builtin(self) -> None:
        hook = PermissionHook(DenyAllPermission())
        with pytest.raises(PermissionDeniedError):
            await hook.pre_execute("t", {}, {"required_permission": "p"})


# ---------------------------------------------------------------------------
# BudgetHook
# ---------------------------------------------------------------------------


class TestBudgetHook:
    async def test_allows_within_budget(self) -> None:
        hook = BudgetHook(max_calls=3)
        for _ in range(3):
            result = await hook.pre_execute("echo", {"msg": "hi"}, {})
            assert result == {"msg": "hi"}

    async def test_rejects_when_budget_exhausted(self) -> None:
        hook = BudgetHook(max_calls=2)
        await hook.pre_execute("echo", {}, {})
        await hook.pre_execute("echo", {}, {})
        with pytest.raises(PermissionDeniedError) as exc_info:
            await hook.pre_execute("echo", {}, {})
        assert "budget:2" in exc_info.value.permission

    async def test_call_count_increments(self) -> None:
        hook = BudgetHook(max_calls=10)
        assert hook.call_count == 0
        await hook.pre_execute("t", {}, {})
        assert hook.call_count == 1
        await hook.pre_execute("t", {}, {})
        assert hook.call_count == 2

    async def test_max_calls_property(self) -> None:
        hook = BudgetHook(max_calls=5)
        assert hook.max_calls == 5

    async def test_first_over_budget_call_raises(self) -> None:
        hook = BudgetHook(max_calls=0)
        with pytest.raises(PermissionDeniedError):
            await hook.pre_execute("t", {}, {})

    async def test_passes_args_through_unmodified(self) -> None:
        hook = BudgetHook(max_calls=5)
        args = {"key": "value"}
        out = await hook.pre_execute("t", args, {})
        assert out == args

    async def test_includes_tool_name_in_error(self) -> None:
        hook = BudgetHook(max_calls=0)
        with pytest.raises(PermissionDeniedError) as exc_info:
            await hook.pre_execute("my_tool", {}, {})
        assert exc_info.value.tool_name == "my_tool"


# ---------------------------------------------------------------------------
# AuditHook
# ---------------------------------------------------------------------------


class TestAuditHook:
    async def test_returns_result_unchanged(self) -> None:
        hook = AuditHook()
        r = _result("output")
        out = await hook.post_execute("echo", {}, r, {})
        assert out is r

    async def test_records_audit_entry(self) -> None:
        hook = AuditHook()
        r = _result("output")
        await hook.post_execute("echo", {"msg": "hi"}, r, {})
        assert len(hook.entries) == 1
        entry = hook.entries[0]
        assert entry.tool_name == "echo"
        assert entry.args == {"msg": "hi"}
        assert entry.result_content == "output"
        assert entry.is_error is False

    async def test_records_error_result(self) -> None:
        hook = AuditHook()
        r = _result("boom", is_error=True)
        await hook.post_execute("fail", {}, r, {})
        assert hook.entries[0].is_error is True

    async def test_accumulates_multiple_entries(self) -> None:
        hook = AuditHook()
        for i in range(5):
            await hook.post_execute("tool", {}, _result(f"result_{i}"), {})
        assert len(hook.entries) == 5

    async def test_elapsed_ms_is_non_negative(self) -> None:
        import time

        hook = AuditHook()
        state = {"_started_at": time.monotonic()}
        await hook.post_execute("t", {}, _result("x"), state)
        assert hook.entries[0].elapsed_ms >= 0.0

    async def test_timestamp_is_set(self) -> None:
        import time

        hook = AuditHook()
        before = time.time()
        await hook.post_execute("t", {}, _result("x"), {})
        after = time.time()
        assert before <= hook.entries[0].timestamp <= after

    async def test_audit_entry_dataclass_fields(self) -> None:
        entry = AuditEntry(
            tool_name="t",
            args={},
            result_content="r",
            is_error=False,
            elapsed_ms=1.5,
        )
        assert entry.tool_name == "t"
        assert entry.elapsed_ms == 1.5

    async def test_entries_is_bounded_deque(self) -> None:
        from collections import deque

        hook = AuditHook(max_entries=3)
        assert isinstance(hook.entries, deque)
        assert hook.entries.maxlen == 3

    async def test_entries_evicts_oldest_when_full(self) -> None:
        hook = AuditHook(max_entries=3)
        for i in range(5):
            await hook.post_execute("t", {}, _result(f"r{i}"), {})
        # deque kept only the last 3
        assert len(hook.entries) == 3
        contents = [e.result_content for e in hook.entries]
        assert contents == ["r2", "r3", "r4"]

    async def test_default_max_entries_is_1000(self) -> None:
        hook = AuditHook()
        assert hook.entries.maxlen == 1000

    async def test_custom_max_entries(self) -> None:
        hook = AuditHook(max_entries=50)
        assert hook.entries.maxlen == 50


# ---------------------------------------------------------------------------
# SanitisationHook
# ---------------------------------------------------------------------------


class TestSanitisationHook:
    async def test_clean_content_returned_as_is(self) -> None:
        hook = SanitisationHook()
        r = _result("The weather is nice today.")
        out = await hook.post_execute("t", {}, r, {})
        assert out is r

    async def test_redacts_api_key_assignment(self) -> None:
        hook = SanitisationHook()
        r = _result("api_key=sk-abc123XYZ890")
        out = await hook.post_execute("t", {}, r, {})
        assert _REDACTED in out.content
        assert "sk-abc123XYZ890" not in out.content

    async def test_redacts_bearer_token(self) -> None:
        hook = SanitisationHook()
        r = _result("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
        out = await hook.post_execute("t", {}, r, {})
        assert _REDACTED in out.content

    async def test_redacts_aws_access_key_id(self) -> None:
        hook = SanitisationHook()
        r = _result("AKIAIOSFODNN7EXAMPLE")
        out = await hook.post_execute("t", {}, r, {})
        assert _REDACTED in out.content

    async def test_redacts_password_assignment(self) -> None:
        hook = SanitisationHook()
        r = _result("password=supersecret123")
        out = await hook.post_execute("t", {}, r, {})
        assert _REDACTED in out.content

    async def test_redacts_private_key_header(self) -> None:
        hook = SanitisationHook()
        r = _result("-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----")
        out = await hook.post_execute("t", {}, r, {})
        assert _REDACTED in out.content

    async def test_preserves_is_error_flag(self) -> None:
        hook = SanitisationHook()
        r = _result("api_key=secret", is_error=True)
        out = await hook.post_execute("t", {}, r, {})
        assert out.is_error is True

    async def test_preserves_tool_call_id(self) -> None:
        hook = SanitisationHook()
        r = ToolResult(tool_call_id="xyz", content="api_key=secret", is_error=False)
        out = await hook.post_execute("t", {}, r, {})
        assert out.tool_call_id == "xyz"

    async def test_custom_patterns(self) -> None:
        pattern = re.compile(r"\bCUSTOM_SECRET\b")
        hook = SanitisationHook(patterns=[pattern])
        r = _result("value is CUSTOM_SECRET here")
        out = await hook.post_execute("t", {}, r, {})
        assert "CUSTOM_SECRET" not in out.content
        assert _REDACTED in out.content

    async def test_custom_redacted_string(self) -> None:
        pattern = re.compile(r"api_key=\S+")
        hook = SanitisationHook(patterns=[pattern], redacted="***")
        r = _result("api_key=abc123")
        out = await hook.post_execute("t", {}, r, {})
        assert "***" in out.content

    async def test_multiple_secrets_in_same_content(self) -> None:
        hook = SanitisationHook()
        r = _result("api_key=abc123 and password=secret99")
        out = await hook.post_execute("t", {}, r, {})
        assert "abc123" not in out.content
        assert "secret99" not in out.content

    async def test_default_patterns_list(self) -> None:
        hook = SanitisationHook()
        assert hook._patterns is _SECRET_PATTERNS


# ---------------------------------------------------------------------------
# HookPipeline
# ---------------------------------------------------------------------------


class TestHookPipeline:
    async def test_empty_pipeline_returns_args_unchanged(self) -> None:
        pipeline = HookPipeline()
        args = {"x": 1}
        out = await pipeline.run_pre("t", args, {})
        assert out == args

    async def test_empty_pipeline_returns_result_unchanged(self) -> None:
        pipeline = HookPipeline()
        r = _result("hello")
        out = await pipeline.run_post("t", {}, r, {})
        assert out is r

    async def test_pre_hooks_run_in_order(self) -> None:
        order: list[str] = []

        class TagHook(PreToolHookPort):
            def __init__(self, tag: str) -> None:
                self._tag = tag

            async def pre_execute(self, tool_name: str, args: dict, state: dict) -> dict:
                order.append(self._tag)
                return args

        pipeline = HookPipeline(pre_hooks=[TagHook("first"), TagHook("second")])
        await pipeline.run_pre("t", {}, {})
        assert order == ["first", "second"]

    async def test_post_hooks_run_in_order(self) -> None:
        order: list[str] = []

        class TagHook(PostToolHookPort):
            def __init__(self, tag: str) -> None:
                self._tag = tag

            async def post_execute(
                self, tool_name: str, args: dict, result: ToolResult, state: dict
            ) -> ToolResult:
                order.append(self._tag)
                return result

        pipeline = HookPipeline(post_hooks=[TagHook("first"), TagHook("second")])
        await pipeline.run_post("t", {}, _result(), {})
        assert order == ["first", "second"]

    async def test_pre_hook_can_modify_args(self) -> None:
        class AddKeyHook(PreToolHookPort):
            async def pre_execute(self, tool_name: str, args: dict, state: dict) -> dict:
                return {**args, "injected": True}

        pipeline = HookPipeline(pre_hooks=[AddKeyHook()])
        out = await pipeline.run_pre("t", {"original": 1}, {})
        assert out == {"original": 1, "injected": True}

    async def test_post_hook_can_modify_result(self) -> None:
        class UpperHook(PostToolHookPort):
            async def post_execute(
                self, tool_name: str, args: dict, result: ToolResult, state: dict
            ) -> ToolResult:
                return ToolResult(
                    tool_call_id=result.tool_call_id,
                    content=result.content.upper(),
                    is_error=result.is_error,
                )

        pipeline = HookPipeline(post_hooks=[UpperHook()])
        r = _result("hello world")
        out = await pipeline.run_post("t", {}, r, {})
        assert out.content == "HELLO WORLD"

    async def test_pre_hook_exception_propagates(self) -> None:
        pipeline = HookPipeline(pre_hooks=[PermissionHook(DenyAllPermission())])
        with pytest.raises(PermissionDeniedError):
            await pipeline.run_pre("t", {}, {"required_permission": "x"})

    async def test_pre_hooks_chain_modified_args(self) -> None:
        class AppendHook(PreToolHookPort):
            def __init__(self, char: str) -> None:
                self._char = char

            async def pre_execute(self, tool_name: str, args: dict, state: dict) -> dict:
                return {**args, "chars": args.get("chars", "") + self._char}

        pipeline = HookPipeline(pre_hooks=[AppendHook("a"), AppendHook("b"), AppendHook("c")])
        out = await pipeline.run_pre("t", {}, {})
        assert out == {"chars": "abc"}

    async def test_properties_return_copies(self) -> None:
        pre = PermissionHook(AllowAllPermission())
        post = AuditHook()
        pipeline = HookPipeline(pre_hooks=[pre], post_hooks=[post])
        pre_list = pipeline.pre_hooks
        post_list = pipeline.post_hooks
        pre_list.append(pre)
        post_list.append(post)
        assert len(pipeline.pre_hooks) == 1
        assert len(pipeline.post_hooks) == 1


# ---------------------------------------------------------------------------
# Registry + HookPipeline integration
# ---------------------------------------------------------------------------


class TestRegistryWithHookPipeline:
    async def test_dispatch_without_pipeline_works(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        result = await registry.dispatch("echo", {"message": "hello"}, "cid1")
        assert result.content == "hello"
        assert not result.is_error

    async def test_dispatch_with_empty_pipeline(self) -> None:
        registry = ToolRegistry(hook_pipeline=HookPipeline())
        registry.register(EchoTool())
        result = await registry.dispatch("echo", {"message": "hi"}, "cid1")
        assert result.content == "hi"

    async def test_dispatch_pre_hook_can_modify_args(self) -> None:
        class OverrideHook(PreToolHookPort):
            async def pre_execute(self, tool_name: str, args: dict, state: dict) -> dict:
                return {"message": "intercepted"}

        pipeline = HookPipeline(pre_hooks=[OverrideHook()])
        registry = ToolRegistry(hook_pipeline=pipeline)
        registry.register(EchoTool())
        result = await registry.dispatch("echo", {"message": "original"}, "cid")
        assert result.content == "intercepted"

    async def test_dispatch_post_hook_can_modify_result(self) -> None:
        class PrefixHook(PostToolHookPort):
            async def post_execute(
                self, tool_name: str, args: dict, result: ToolResult, state: dict
            ) -> ToolResult:
                return ToolResult(
                    tool_call_id=result.tool_call_id,
                    content="PREFIX:" + result.content,
                    is_error=result.is_error,
                )

        pipeline = HookPipeline(post_hooks=[PrefixHook()])
        registry = ToolRegistry(hook_pipeline=pipeline)
        registry.register(EchoTool())
        result = await registry.dispatch("echo", {"message": "hello"}, "cid")
        assert result.content == "PREFIX:hello"

    async def test_dispatch_permission_hook_blocks_tool(self) -> None:
        pipeline = HookPipeline(pre_hooks=[PermissionHook(DenyAllPermission())])
        registry = ToolRegistry(hook_pipeline=pipeline)
        registry.register(EchoTool())
        result = await registry.dispatch(
            "echo",
            {"message": "hi"},
            "cid",
            agent_state={"required_permission": "tool:echo"},
        )
        assert result.is_error
        assert "denied" in result.content.lower()

    async def test_dispatch_sets_required_permission_in_state(self) -> None:
        observed: list[str] = []

        class ObserveHook(PreToolHookPort):
            async def pre_execute(self, tool_name: str, args: dict, state: dict) -> dict:
                observed.append(state.get("required_permission", ""))
                return args

        pipeline = HookPipeline(pre_hooks=[ObserveHook()])
        registry = ToolRegistry(hook_pipeline=pipeline)
        registry.register(EchoTool())
        await registry.dispatch("echo", {"message": "x"}, "cid")
        assert observed == ["tool:echo"]

    async def test_dispatch_sets_started_at_in_state(self) -> None:
        observed: list[float] = []

        class ObserveHook(PreToolHookPort):
            async def pre_execute(self, tool_name: str, args: dict, state: dict) -> dict:
                observed.append(state.get("_started_at", -1.0))
                return args

        pipeline = HookPipeline(pre_hooks=[ObserveHook()])
        registry = ToolRegistry(hook_pipeline=pipeline)
        registry.register(EchoTool())
        await registry.dispatch("echo", {"message": "x"}, "cid")
        assert observed[0] > 0

    async def test_dispatch_unknown_tool_returns_error(self) -> None:
        registry = ToolRegistry(hook_pipeline=HookPipeline())
        result = await registry.dispatch("unknown", {}, "cid")
        assert result.is_error
        assert "Unknown tool" in result.content

    async def test_dispatch_failing_tool_returns_error(self) -> None:
        registry = ToolRegistry(hook_pipeline=HookPipeline())
        registry.register(FailingTool())
        result = await registry.dispatch("fail", {}, "cid")
        assert result.is_error

    async def test_dispatch_audit_hook_records_entry(self) -> None:
        audit = AuditHook()
        pipeline = HookPipeline(post_hooks=[audit])
        registry = ToolRegistry(hook_pipeline=pipeline)
        registry.register(EchoTool())
        await registry.dispatch("echo", {"message": "hello"}, "cid")
        assert len(audit.entries) == 1
        assert audit.entries[0].tool_name == "echo"

    async def test_dispatch_sanitisation_hook_redacts_output(self) -> None:
        class SecretTool(EchoTool):
            async def execute(self, input: dict) -> ToolResult:
                return ToolResult(
                    tool_call_id="",
                    content="api_key=sk-abc123secretXYZ",
                )

        pipeline = HookPipeline(post_hooks=[SanitisationHook()])
        registry = ToolRegistry(hook_pipeline=pipeline)
        registry.register(SecretTool())
        result = await registry.dispatch("echo", {}, "cid")
        assert "sk-abc123secretXYZ" not in result.content
        assert _REDACTED in result.content

    async def test_dispatch_budget_hook_blocks_after_limit(self) -> None:
        budget = BudgetHook(max_calls=2)
        pipeline = HookPipeline(pre_hooks=[budget])
        registry = ToolRegistry(hook_pipeline=pipeline)
        registry.register(EchoTool())

        r1 = await registry.dispatch("echo", {"message": "a"}, "c1")
        r2 = await registry.dispatch("echo", {"message": "b"}, "c2")
        r3 = await registry.dispatch("echo", {"message": "c"}, "c3")

        assert not r1.is_error
        assert not r2.is_error
        assert r3.is_error

    async def test_dispatch_call_id_preserved_in_result(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        result = await registry.dispatch("echo", {"message": "x"}, "my-call-id")
        assert result.tool_call_id == "my-call-id"

    async def test_dispatch_agent_state_not_mutated(self) -> None:
        registry = ToolRegistry(hook_pipeline=HookPipeline())
        registry.register(EchoTool())
        state = {"custom": "value"}
        await registry.dispatch("echo", {"message": "x"}, "cid", agent_state=state)
        assert state == {"custom": "value"}

    async def test_dispatch_none_agent_state_works(self) -> None:
        registry = ToolRegistry(hook_pipeline=HookPipeline())
        registry.register(EchoTool())
        result = await registry.dispatch("echo", {"message": "hi"}, "cid", agent_state=None)
        assert not result.is_error

    async def test_full_pipeline_pre_and_post(self) -> None:
        audit = AuditHook()
        sanitise = SanitisationHook()
        pipeline = HookPipeline(
            pre_hooks=[PermissionHook(AllowAllPermission()), BudgetHook(max_calls=10)],
            post_hooks=[audit, sanitise],
        )
        registry = ToolRegistry(hook_pipeline=pipeline)
        registry.register(EchoTool())
        result = await registry.dispatch("echo", {"message": "clean output"}, "cid")
        assert result.content == "clean output"
        assert len(audit.entries) == 1
