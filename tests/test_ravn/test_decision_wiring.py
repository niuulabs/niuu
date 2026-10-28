"""Composition of the decision port: pluggable adapter, strict secrets, no silent degradation."""

from __future__ import annotations

import pytest

from ravn.adapters.decision.systemone import SystemOneDecisionAdapter
from ravn.cli.runtime_builders import build_decision_port, build_resident_first_opinion
from ravn.config import Settings
from ravn.decision_observability import ObservedDecisionPort
from ravn.domain.exceptions import ConfigurationError
from ravn.resident_first_opinion import ResidentFirstOpinion


class RecordingAdapter:
    """A custom adapter, selected by dotted path like any other."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def decide(self, state, questions, *, purpose):  # pragma: no cover - not called
        raise AssertionError("not called")


def _settings(**decision) -> Settings:
    return Settings.model_validate({"decision": decision})


def test_disabled_builds_nothing() -> None:
    assert build_decision_port(_settings()) is None


def test_first_opinion_without_a_decision_model_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="decision.enabled is false"):
        build_decision_port(_settings(resident_first_opinion={"enabled": True}))


def test_default_adapter_is_systemone_and_observed() -> None:
    port = build_decision_port(
        _settings(enabled=True, kwargs={"model": "clef-flash", "base_url": "http://ollama:11434"})
    )

    assert isinstance(port, ObservedDecisionPort)
    assert isinstance(port.inner, SystemOneDecisionAdapter)
    assert port.inner._model == "clef-flash"
    assert port.inner._base_url == "http://ollama:11434"


def test_adapter_is_pluggable_and_secrets_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_TYPESAFE_KEY", " sk-123 ")
    port = build_decision_port(
        _settings(
            enabled=True,
            adapter=f"{__name__}.RecordingAdapter",
            kwargs={"model": "jev-latest"},
            secret_kwargs_env={"api_key": "TEST_TYPESAFE_KEY"},
        )
    )

    assert isinstance(port.inner, RecordingAdapter)
    assert port.inner.kwargs == {"model": "jev-latest", "api_key": "sk-123"}


def test_unset_secret_is_a_startup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_TYPESAFE_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="TEST_TYPESAFE_KEY"):
        build_decision_port(
            _settings(enabled=True, secret_kwargs_env={"api_key": "TEST_TYPESAFE_KEY"})
        )


def test_unbuildable_adapter_is_a_startup_error() -> None:
    with pytest.raises(ConfigurationError, match="could not be constructed"):
        build_decision_port(_settings(enabled=True, adapter="ravn.adapters.decision.nope.Missing"))


class TestResidentFirstOpinion:
    def test_off_by_default(self) -> None:
        assert build_resident_first_opinion(_settings(), has_resident_runtime=True) is None

    def test_decision_port_is_still_built_and_checked(self) -> None:
        with pytest.raises(ConfigurationError, match="could not be constructed"):
            build_resident_first_opinion(
                _settings(enabled=True, adapter="ravn.adapters.decision.nope.Missing"),
                has_resident_runtime=True,
            )

    def test_needs_a_resident_runtime(self) -> None:
        with pytest.raises(ConfigurationError, match="no resident runtime"):
            build_resident_first_opinion(
                _settings(enabled=True, resident_first_opinion={"enabled": True}),
                has_resident_runtime=False,
            )

    def test_built_over_the_observed_port(self) -> None:
        first_opinion = build_resident_first_opinion(
            _settings(
                enabled=True,
                resident_first_opinion={"enabled": True, "max_observation_chars": 1234},
            ),
            has_resident_runtime=True,
        )

        assert isinstance(first_opinion, ResidentFirstOpinion)
        assert isinstance(first_opinion._decisions, ObservedDecisionPort)
        assert first_opinion._max_observation_chars == 1234
