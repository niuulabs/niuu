"""Capability-aware Ravn harness selection tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from niuu.ports.cli import CLITransport, TransportCapabilities
from ravn.adapters.personas.loader import (
    FilesystemPersonaAdapter,
    PersonaConfig,
    PersonaExecutorConfig,
    PersonaExecutorFallback,
)
from ravn.cli.runtime_builders import _build_executor, emit_harness_selection
from ravn.domain.harness import (
    HarnessCapability,
    HarnessKind,
    HarnessRequirementError,
    SteeringMode,
)
from tests.test_ravn.conftest import make_simple_llm


class ResumeTransport(CLITransport):
    """A CLI transport whose startup is visible to selection tests."""

    starts = 0

    def __init__(
        self,
        workspace_dir: str,
        *,
        model: str = "",
        resume_session_id: str = "",
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self.model = model
        self.resume_session_id = resume_session_id

    async def start(self) -> None:
        type(self).starts += 1

    async def stop(self) -> None:
        return None

    async def send_message(self, content: str) -> None:
        return None

    @property
    def session_id(self) -> str | None:
        return None

    @property
    def last_result(self) -> dict | None:
        return None

    @property
    def is_alive(self) -> bool:
        return False

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(session_resume=True)


_CLI_EXECUTOR = "ravn.adapters.executors.cli.CliTransportExecutor"
_RESUME_TRANSPORT = "tests.test_ravn.test_harness_selection.ResumeTransport"
_NATIVE_STEERING_TRANSPORTS = (
    "skuld.transports.pi.PiRpcTransport",
    "skuld.transports.tmux_interactive.TmuxInteractiveTransport",
    "skuld.transports.muse.MuseMSPTransport",
)


def _persona(executor: PersonaExecutorConfig) -> PersonaConfig:
    return PersonaConfig(name="selection-test", executor=executor)


def test_native_harness_is_selected_when_it_meets_canonical_requirements() -> None:
    executor = _build_executor(
        _persona(
            PersonaExecutorConfig(
                requires=frozenset({HarnessCapability.TOOLS, HarnessCapability.DYNAMIC_TOOLS})
            )
        )
    )

    selection = executor.harness_selection

    assert selection.profile.kind is HarnessKind.NATIVE
    assert selection.requirements == frozenset(
        {HarnessCapability.TOOLS, HarnessCapability.DYNAMIC_TOOLS}
    )
    assert selection.reason == "configured"


def test_compatible_cli_harness_is_selected_without_starting_provider_session() -> None:
    ResumeTransport.starts = 0
    executor = _build_executor(
        _persona(
            PersonaExecutorConfig(
                adapter=_CLI_EXECUTOR,
                kwargs={"transport_adapter": _RESUME_TRANSPORT},
                requires=frozenset({HarnessCapability.SESSION_RESUME}),
            )
        )
    )

    assert executor.harness_selection.profile.kind is HarnessKind.CLI_BACKED
    assert HarnessCapability.SESSION_RESUME in executor.harness_selection.capabilities
    assert ResumeTransport.starts == 0


@pytest.mark.parametrize("transport_adapter", _NATIVE_STEERING_TRANSPORTS)
def test_no_requirements_selects_native_steering_cli_transport(
    transport_adapter: str,
) -> None:
    executor = _build_executor(
        _persona(
            PersonaExecutorConfig(
                adapter=_CLI_EXECUTOR,
                kwargs={"transport_adapter": transport_adapter},
            )
        )
    )

    selection = executor.harness_selection

    assert selection.requirements == frozenset()
    assert selection.profile.steering_mode is SteeringMode.NATIVE
    assert HarnessCapability.STEERING in selection.capabilities


def test_incompatible_explicit_cli_harness_fails_with_every_missing_requirement() -> None:
    persona = _persona(
        PersonaExecutorConfig(
            adapter=_CLI_EXECUTOR,
            kwargs={"transport_adapter": _RESUME_TRANSPORT},
            requires=frozenset({HarnessCapability.REASONING, HarnessCapability.IMAGES}),
        )
    )

    with pytest.raises(HarnessRequirementError) as error:
        _build_executor(persona)

    message = str(error.value)
    assert _CLI_EXECUTOR in message
    assert "reasoning" in message
    assert "images" in message
    assert ResumeTransport.starts == 0


def test_pre_start_fallback_must_satisfy_every_requirement() -> None:
    executor = _build_executor(
        _persona(
            PersonaExecutorConfig(
                adapter="ravn.adapters.executors.agent.AgentExecutor",
                requires=frozenset({HarnessCapability.SESSION_RESUME}),
                fallbacks=[
                    PersonaExecutorFallback(
                        adapter=_CLI_EXECUTOR,
                        kwargs={"transport_adapter": _RESUME_TRANSPORT},
                    )
                ],
            )
        )
    )

    selection = executor.harness_selection

    assert selection.adapter == _CLI_EXECUTOR
    assert selection.reason == "fallback_after_incompatible_primary"
    assert selection.requirements <= selection.capabilities


async def test_cli_primary_fallback_to_native_builds_and_runs_with_llm() -> None:
    """A native fallback gets native resources, not those of the CLI primary."""
    from ravn.cli.commands import _build_agent
    from ravn.config import Settings

    llm = make_simple_llm("native fallback response")
    persona = _persona(
        PersonaExecutorConfig(
            adapter=_CLI_EXECUTOR,
            kwargs={"transport_adapter": _RESUME_TRANSPORT},
            requires=frozenset({HarnessCapability.REASONING}),
            fallbacks=[
                PersonaExecutorFallback(
                    adapter="ravn.adapters.executors.agent.AgentExecutor",
                )
            ],
        )
    )

    with patch("ravn.cli.commands._build_llm", return_value=llm):
        agent, _ = _build_agent(Settings(), persona_config=persona)

    assert agent._llm is llm
    result = await agent.run_turn("use the native fallback")
    assert result.response == "native fallback response"


def test_incompatible_fallback_is_not_selected() -> None:
    persona = _persona(
        PersonaExecutorConfig(
            adapter="ravn.adapters.executors.agent.AgentExecutor",
            requires=frozenset({HarnessCapability.SESSION_RESUME}),
            fallbacks=[
                PersonaExecutorFallback(
                    adapter="ravn.adapters.executors.agent.AgentExecutor",
                )
            ],
        )
    )

    with pytest.raises(HarnessRequirementError) as error:
        _build_executor(persona)

    assert str(error.value).count("missing session_resume") == 2


def test_mixed_runtime_flock_composition_resolves_each_persona_independently() -> None:
    native = _build_executor(
        _persona(PersonaExecutorConfig(requires=frozenset({HarnessCapability.REASONING})))
    )
    cli = _build_executor(
        _persona(
            PersonaExecutorConfig(
                adapter=_CLI_EXECUTOR,
                kwargs={"transport_adapter": _RESUME_TRANSPORT},
                requires=frozenset({HarnessCapability.SESSION_RESUME}),
            )
        )
    )

    assert native.harness_selection.profile.kind is HarnessKind.NATIVE
    assert cli.harness_selection.profile.kind is HarnessKind.CLI_BACKED
    assert native.harness_selection.capabilities != cli.harness_selection.capabilities


def test_no_requirements_preserves_existing_default_and_emits_safe_selection() -> None:
    executor = _build_executor(_persona(PersonaExecutorConfig()))
    selection = emit_harness_selection(executor, model="safe-model")

    assert type(executor).__name__ == "AgentExecutor"
    assert selection is not None
    assert selection.to_dict() == {
        "adapter": "ravn.adapters.executors.agent.AgentExecutor",
        "runtime_identity": "ravn.adapters.executors.agent.AgentExecutor",
        "model": "safe-model",
        "capabilities": ["dynamic_tools", "interrupt", "reasoning", "tools"],
        "requires": [],
        "reason": "configured",
    }


def test_persona_requires_are_validated_at_load_time() -> None:
    with pytest.raises(ValueError, match="unknown harness capability"):
        FilesystemPersonaAdapter.parse(
            """\
name: invalid
executor:
  requires: [provider_magic]
"""
        )
