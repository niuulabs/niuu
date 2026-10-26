from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from niuu.ports.cli import CLITransport, TransportCapabilities
from ravn.adapters.permission.allow_deny import AllowAllPermission, DenyAllPermission
from ravn.agent import RavnAgent
from ravn.domain.checkpoint import InterruptReason
from ravn.domain.exceptions import MaxIterationsError
from ravn.domain.harness import (
    HarnessCapabilityProfile,
    HarnessContractError,
    HarnessControl,
    HarnessControlKind,
    HarnessKind,
    HarnessLifecycleError,
    HarnessTurnInput,
    HarnessTurnResult,
    SteeringMode,
)
from ravn.domain.models import (
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
    ToolResult,
)
from ravn.ports.llm import LLMPort
from tests.ravn.fixtures.fakes import EchoTool
from tests.test_ravn.conftest import InMemoryChannel, make_simple_llm
from tests.test_ravn.test_executor_cli import (
    FakeStatelessTransport,
    _make_agent,
    _TransportBinding,
)


def test_native_and_cli_harness_profiles_are_representable_without_runtime_types() -> None:
    native = HarnessCapabilityProfile.native_ravn_agent()
    cli = HarnessCapabilityProfile.cli_backed(
        supports_reasoning=True,
        supports_attachments=True,
        supports_images=True,
        supports_interrupt=True,
        steering_mode=SteeringMode.INTERRUPT_RESUME,
        supports_session_resume=True,
    )

    assert native.kind is HarnessKind.NATIVE
    assert native.supports_dynamic_tools is True
    assert native.supports_steering is False
    assert cli.kind is HarnessKind.CLI_BACKED
    assert cli.supports_images is True
    assert cli.supports_session_resume is True
    assert cli.supports_steering is True


@pytest.mark.parametrize(
    ("profile", "message"),
    [
        (
            {
                "supports_images": True,
            },
            "image input requires attachment input support",
        ),
        (
            {
                "supports_dynamic_tools": True,
            },
            "dynamic tool exposure requires tool execution support",
        ),
        (
            {
                "supports_interrupt": False,
                "steering_mode": SteeringMode.INTERRUPT_RESUME,
            },
            "interrupt-resume steering requires interrupt support",
        ),
        (
            {
                "steering_mode": "interrupt_resume",
            },
            "steering mode must be a SteeringMode",
        ),
    ],
)
def test_invalid_harness_capability_profiles_fail_deterministically(
    profile: dict[str, object], message: str
) -> None:
    defaults: dict[str, object] = {
        "kind": HarnessKind.CLI_BACKED,
        "supports_tools": False,
        "supports_reasoning": False,
        "supports_attachments": False,
        "supports_images": False,
        "supports_interrupt": False,
        "steering_mode": SteeringMode.NONE,
        "supports_session_resume": False,
        "supports_dynamic_tools": False,
    }

    with pytest.raises(HarnessContractError, match=message):
        HarnessCapabilityProfile(**(defaults | profile))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "control",
    [
        HarnessControl(HarnessControlKind.INTERRUPT, interrupt_reason=InterruptReason.SIGINT),
        HarnessControl(HarnessControlKind.STEER, content="use the safer plan"),
    ],
)
def test_harness_controls_represent_supported_control_requests(control: HarnessControl) -> None:
    assert control.kind in {HarnessControlKind.INTERRUPT, HarnessControlKind.STEER}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"kind": HarnessControlKind.INTERRUPT},
            "interrupt control requires an interrupt reason",
        ),
        (
            {"kind": HarnessControlKind.STEER},
            "steering control requires nonblank content",
        ),
        (
            {"kind": "interrupt"},
            "control kind must be a HarnessControlKind",
        ),
        (
            {"kind": "unexpected"},
            "control kind must be a HarnessControlKind",
        ),
        (
            {"kind": HarnessControlKind.STEER, "content": None},
            "control content must be a str",
        ),
        (
            {"kind": HarnessControlKind.INTERRUPT, "interrupt_reason": "sigint"},
            "interrupt reason must be an InterruptReason",
        ),
    ],
)
def test_invalid_harness_controls_fail_deterministically(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(HarnessContractError, match=message):
        HarnessControl(**kwargs)  # type: ignore[arg-type]


def test_ravn_domain_and_ports_do_not_import_skuld_implementations() -> None:
    import ast
    from pathlib import Path

    source_root = Path(__file__).resolve().parents[2] / "src" / "ravn"
    violations: list[str] = []
    for directory in (source_root / "domain", source_root / "ports"):
        for path in directory.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported = [node.module]
                else:
                    continue
                if any(name == "skuld" or name.startswith("skuld.") for name in imported):
                    violations.append(str(path.relative_to(source_root)))

    assert violations == []


@pytest.mark.asyncio
async def test_native_harness_returns_the_same_canonical_turn_shape_as_cli() -> None:
    llm = make_simple_llm("Done")
    agent = RavnAgent(
        llm=llm,
        tools=[],
        channel=InMemoryChannel(),
        permission=AllowAllPermission(),
        system_prompt="You are a reviewer.",
        model="fake-model",
        max_tokens=100,
        max_iterations=2,
    )

    with pytest.raises(HarnessLifecycleError, match="started"):
        await agent.run_turn(HarnessTurnInput("Review the patch"))

    await agent.start()
    result = await agent.run_turn(HarnessTurnInput("Review the patch"))

    assert isinstance(result, HarnessTurnResult)
    assert result.turn.response == "Done"
    assert result.turn.episode is not None
    assert result.turn.usage.input_tokens == 10
    assert result.turn.usage.output_tokens == 5
    assert result.metadata == {"provider_adapter": "AsyncMock"}


@pytest.mark.asyncio
async def test_native_harness_rejects_resume_and_inactive_controls() -> None:
    agent = RavnAgent(
        llm=AsyncMock(spec=LLMPort),
        tools=[],
        channel=InMemoryChannel(),
        permission=AllowAllPermission(),
        system_prompt="You are a reviewer.",
        model="fake-model",
        max_tokens=100,
        max_iterations=2,
    )
    await agent.start()

    with pytest.raises(HarnessContractError, match="session resume"):
        await agent.run_turn(HarnessTurnInput("Review", provider_session_id="old"))
    with pytest.raises(HarnessLifecycleError, match="active turn"):
        await agent.control(
            HarnessControl(HarnessControlKind.INTERRUPT, interrupt_reason=InterruptReason.SIGINT)
        )


class _ContractEchoTool(EchoTool):
    """Echo tool that returns the call ID supplied in the canonical fixture."""

    async def execute(self, input: dict) -> ToolResult:
        return ToolResult(tool_call_id=input["call_id"], content="ok")


_SCENARIO_TOOLS: dict[str, tuple[list[ToolCall], list[ToolResult]]] = {
    "success": (
        [ToolCall("tool-ok", "echo", {"call_id": "tool-ok"})],
        [ToolResult("tool-ok", "ok")],
    ),
    "partial": (
        [
            ToolCall("tool-ok", "echo", {"call_id": "tool-ok"}),
            ToolCall("tool-error", "missing", {}),
        ],
        [
            ToolResult("tool-ok", "ok"),
            ToolResult("tool-error", "Unknown tool: missing", is_error=True),
        ],
    ),
    "approval_blocked": (
        [ToolCall("tool-denied", "echo", {})],
        [
            ToolResult(
                "tool-denied",
                "Permission 'tool:echo' denied for tool 'echo'",
                is_error=True,
            )
        ],
    ),
}


class _ContractScenarioTransport(CLITransport):
    """CLI transport whose event script mirrors native harness scenario inputs."""

    def __init__(
        self,
        workspace_dir: str,
        *,
        model: str = "",
        scenario: str = "success",
        resume_session_id: str = "",
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self.model = model
        self.scenario = scenario
        self.resume_session_id = resume_session_id
        self._session_id = ""
        self._last_result: dict | None = None
        self.stopped = False
        self.sent_messages: list[str] = []

    async def start(self) -> None:
        self._session_id = self.resume_session_id or "cli-session"

    async def stop(self) -> None:
        self.stopped = True

    async def send_message(self, content: str) -> None:
        self.sent_messages.append(content)
        if self.scenario == "provider_failure":
            raise RuntimeError("provider failed")
        tool_calls, tool_results = _SCENARIO_TOOLS[self.scenario]
        await self._emit(
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
                    for call in tool_calls
                ],
            }
        )
        await self._emit(
            {
                "type": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": result.tool_call_id,
                        "content": result.content,
                        "is_error": result.is_error,
                    }
                    for result in tool_results
                ],
            }
        )
        self._last_result = {
            "type": "result",
            "result": "Done",
            "stop_reason": "end_turn",
            "modelUsage": {"fake-model": {"inputTokens": 20, "outputTokens": 10}},
        }
        await self._emit(self._last_result)

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return True

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(session_resume=True, interrupt=True)


class _InterruptibleContractTransport(_ContractScenarioTransport):
    def __init__(
        self,
        workspace_dir: str,
        *,
        model: str = "",
        scenario: str = "success",
        resume_session_id: str = "",
    ) -> None:
        super().__init__(
            workspace_dir,
            model=model,
            scenario=scenario,
            resume_session_id=resume_session_id,
        )
        self.turn_started = asyncio.Event()
        self.release_turn = asyncio.Event()
        self.interrupted = False

    async def send_message(self, content: str) -> None:
        self.sent_messages.append(content)
        self.turn_started.set()
        await self.release_turn.wait()
        if self.interrupted:
            raise RuntimeError("turn interrupted")
        await super().send_message(content)

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        if subtype == "interrupt":
            self.interrupted = True


def _make_contract_harness(
    implementation: str, scenario: str
) -> tuple[RavnAgent | object, _ContractScenarioTransport | None]:
    if implementation == "cli":
        agent = _make_agent(
            binding=_TransportBinding(_ContractScenarioTransport, True),
        )[0]
        agent._transport_kwargs["scenario"] = scenario
        return agent, None

    llm = AsyncMock(spec=LLMPort)
    stream_count = 0

    async def stream(*args: object, **kwargs: object) -> AsyncIterator[StreamEvent]:
        nonlocal stream_count
        stream_count += 1
        if scenario == "provider_failure":
            raise RuntimeError("provider failed")
        tool_calls, _ = _SCENARIO_TOOLS[scenario]
        if stream_count == 1:
            for tool_call in tool_calls:
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)
        else:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="Done")
        yield StreamEvent(type=StreamEventType.MESSAGE_DONE, usage=TokenUsage(10, 5))

    llm.stream = stream
    agent = RavnAgent(
        llm=llm,
        tools=[_ContractEchoTool()],
        channel=InMemoryChannel(),
        permission=DenyAllPermission() if scenario == "approval_blocked" else AllowAllPermission(),
        system_prompt="You are a reviewer.",
        model="fake-model",
        max_tokens=100,
        max_iterations=3,
    )
    return agent, None


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ["native", "cli"])
@pytest.mark.parametrize(
    ("scenario", "outcome", "usage"),
    [
        ("success", "success", (20, 10)),
        ("partial", "partial", (20, 10)),
        ("approval_blocked", "failure", (20, 10)),
    ],
)
async def test_canonical_scenario_contract_runs_on_both_implementations(
    implementation: str, scenario: str, outcome: str, usage: tuple[int, int]
) -> None:
    """Exercise canonical success, partial, and approval-blocked turn semantics."""
    harness, _ = _make_contract_harness(implementation, scenario)

    await harness.start()
    result = await harness.run_turn(HarnessTurnInput("Review the patch"))

    assert isinstance(result, HarnessTurnResult)
    assert result.turn.response == "Done"
    assert result.turn.episode is not None
    assert result.turn.episode.outcome.value == outcome
    assert (result.turn.usage.input_tokens, result.turn.usage.output_tokens) == usage
    assert {item.tool_call_id for item in result.turn.tool_results} == {
        call.id for call in result.turn.tool_calls
    }
    assert result.metadata

    await harness.close()
    with pytest.raises(HarnessLifecycleError, match="closed"):
        await harness.run_turn(HarnessTurnInput("Review the patch"))


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ["native", "cli"])
async def test_canonical_provider_failure_contract_runs_on_both_implementations(
    implementation: str,
) -> None:
    """Provider failures remain terminal rather than becoming successful turns."""
    harness, _ = _make_contract_harness(implementation, "provider_failure")
    await harness.start()

    with pytest.raises(RuntimeError, match="provider failed"):
        await harness.run_turn(HarnessTurnInput("Review the patch"))


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ["native", "cli"])
async def test_canonical_control_requires_an_active_turn_for_both_implementations(
    implementation: str,
) -> None:
    harness, _ = _make_contract_harness(implementation, "success")
    await harness.start()

    with pytest.raises(HarnessLifecycleError, match="active turn"):
        await harness.control(
            HarnessControl(HarnessControlKind.INTERRUPT, interrupt_reason=InterruptReason.SIGINT)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ["native", "cli"])
async def test_canonical_interrupted_turn_is_terminal_for_both_implementations(
    implementation: str,
) -> None:
    """A supported interrupt control terminates an active canonical turn."""
    if implementation == "native":
        started = asyncio.Event()
        release = asyncio.Event()
        llm = AsyncMock(spec=LLMPort)

        async def stream(*args: object, **kwargs: object) -> AsyncIterator[StreamEvent]:
            started.set()
            await release.wait()
            yield StreamEvent(
                type=StreamEventType.TOOL_CALL, tool_call=ToolCall("tool", "missing", {})
            )
            yield StreamEvent(type=StreamEventType.MESSAGE_DONE, usage=TokenUsage(1, 1))

        llm.stream = stream
        harness = RavnAgent(
            llm=llm,
            tools=[],
            channel=InMemoryChannel(),
            permission=AllowAllPermission(),
            system_prompt="You are a reviewer.",
            model="fake-model",
            max_tokens=100,
            max_iterations=2,
        )
        transport = None
    else:
        harness = _make_agent(binding=_TransportBinding(_InterruptibleContractTransport, True))[0]
        transport = None

    await harness.start()
    running_turn = asyncio.create_task(harness.run_turn(HarnessTurnInput("Review the patch")))
    if implementation == "native":
        await started.wait()
    else:
        assert isinstance(harness._transport, _InterruptibleContractTransport)
        transport = harness._transport
        await transport.turn_started.wait()

    assert await harness.control(
        HarnessControl(HarnessControlKind.INTERRUPT, interrupt_reason=InterruptReason.SIGINT)
    )
    if transport is not None:
        await asyncio.sleep(0)
        assert transport.interrupted is True
        transport.release_turn.set()
    else:
        release.set()

    with pytest.raises((MaxIterationsError, RuntimeError)):
        await running_turn


@pytest.mark.asyncio
async def test_canonical_resume_uses_cli_provider_state_and_rejects_native_resume() -> None:
    native, _ = _make_contract_harness("native", "success")
    await native.start()
    with pytest.raises(HarnessContractError, match="session resume"):
        await native.run_turn(HarnessTurnInput("Review", provider_session_id="old"))

    cli, _ = _make_contract_harness("cli", "success")
    await cli.start()
    result = await cli.run_turn(HarnessTurnInput("Review", provider_session_id="old"))

    assert result.provider_session_id == "old"
    assert result.metadata["provider_result"]["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_canonical_cli_transcript_reconstructs_when_provider_resume_is_unavailable() -> None:
    agent, _ = _make_agent(binding=_TransportBinding(FakeStatelessTransport, False))
    agent.session.add_message(
        __import__("ravn.domain.models", fromlist=["Message"]).Message(
            role="assistant", content="Earlier answer"
        )
    )
    await agent.start()

    await agent.run_turn(HarnessTurnInput("Write the fix"))

    transport = agent._transport
    assert isinstance(transport, FakeStatelessTransport)
    assert "Conversation so far:\nAssistant:\nEarlier answer" in transport.sent_messages[0]
    assert "User:\nWrite the fix" in transport.sent_messages[0]
