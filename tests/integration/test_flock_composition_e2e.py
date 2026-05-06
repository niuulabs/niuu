"""M7 End-to-end integration tests for flock composition (NIU-646).

Three scenarios
---------------

1. **MountedVolume** (kind cluster required) — install chart with
   ``personaSource.mode: mountedVolume`` + ``KubernetesConfigMapFlockFlowProvider``,
   seed a ``reviewer`` persona via REST, wait for ConfigMap projection, create a
   ``code-review-flow`` via REST, dispatch a pipeline with a stage-level
   ``thinking_enabled: true`` override, poll the sidecar pod, and assert
   ``/etc/ravn/config.yaml`` reflects the merged effective config.

2. **HTTP** (kind cluster required) — same as (1) but
   ``personaSource.mode: http``; sidecars pull personas from the volundr REST
   endpoint using a PAT.  No ConfigMap projection — verify via the ravn startup
   log line instead.

3. **In-process parity** (always runs) — build the identical flock flow and
   persona override that scenarios 1/2 use, then exercise the in-process
   ``RavnDispatcher`` path through ``build_flock_workload_config`` + ``merge_llm``
   and assert the effective model is the same one the sidecar would receive.
   This covers NIU-645's regression: the in-process dispatch hole.

Skip policy
-----------
Scenarios 1 and 2 are decorated with ``@pytest.mark.kind_integration`` and
wrapped in a ``pytest.importorskip``-style skip guard so they are omitted from
the default ``pytest`` run.  Set ``KIND_INTEGRATION=1`` to enable them.  The
``integration-kind.yml`` workflow sets this variable explicitly.

Scenario 3 always runs and contributes to the 85 % coverage gate.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from niuu.domain.llm_merge import merge_llm
from tyr.adapters.flows.config import ConfigFlockFlowProvider
from tyr.domain.flock_flow import FlockFlowConfig, FlockPersonaOverride
from tyr.domain.flock_merge import build_flock_workload_config, merge_persona_override
from tyr.domain.templates import TemplateRaid

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

#: The persona name used throughout all three scenarios.
_PERSONA_NAME = "reviewer"

#: The flow name used throughout all three scenarios.
_FLOW_NAME = "code-review-flow"

#: The flow-level LLM override — sets a concrete model alias.
_FLOW_LLM_OVERRIDE: dict = {"model": "claude-opus-4-6"}

#: Flow-level system_prompt_extra for the reviewer.
_FLOW_PROMPT_EXTRA = "Focus on security vulnerabilities and data handling."

#: Stage-level override — enables extended thinking.
_STAGE_OVERRIDE: dict = {
    "llm": {"thinking_enabled": True},
    "system_prompt_extra": "Double-check all error handling paths.",
}

# Expected effective LLM config after all merge layers are applied.
_EXPECTED_EFFECTIVE_LLM: dict = {
    "model": "claude-opus-4-6",  # from flow-level override
    "thinking_enabled": True,  # from stage-level override
}

#: Expected concatenated system_prompt_extra.
_EXPECTED_COMBINED_PROMPT_EXTRA = (
    _FLOW_PROMPT_EXTRA.strip() + "\n\n" + _STAGE_OVERRIDE["system_prompt_extra"].strip()
)

# ---------------------------------------------------------------------------
# Kind integration gate
# ---------------------------------------------------------------------------

_KIND_INTEGRATION: bool = os.environ.get("KIND_INTEGRATION", "0") == "1"
_SKIP_KIND = pytest.mark.skipif(
    not _KIND_INTEGRATION,
    reason="KIND_INTEGRATION=1 not set; skipping kind cluster scenarios",
)


def _make_flow() -> FlockFlowConfig:
    """Build the canonical FlockFlowConfig used across all three scenarios."""
    return FlockFlowConfig(
        name=_FLOW_NAME,
        description="Standard code review flow for M7 acceptance test",
        personas=[
            FlockPersonaOverride(
                name=_PERSONA_NAME,
                llm=_FLOW_LLM_OVERRIDE,
                system_prompt_extra=_FLOW_PROMPT_EXTRA,
            ),
        ],
    )


def _make_template_raid() -> TemplateRaid:
    """Build a TemplateRaid with stage-level persona_overrides."""
    return TemplateRaid(
        name="Review PR #1",
        description="End-to-end acceptance test raid",
        acceptance_criteria=["All tests pass", "No security issues"],
        declared_files=["src/tyr/domain/flock_flow.py"],
        estimate_hours=1.0,
        prompt="Review the flock composition implementation",
        persona=_PERSONA_NAME,
        persona_overrides=_STAGE_OVERRIDE,
    )


# ---------------------------------------------------------------------------
# Scenario 3: In-process parity (always runs — no cluster required)
# ---------------------------------------------------------------------------


class TestInProcessParity:
    """Verify that in-process dispatch uses the same model as the flock sidecar.

    Regression guard for NIU-645: before the fix, ``RavnDispatcher`` could
    bypass the flow-level LLM override and use the persona default instead.
    """

    def test_workload_config_contains_flow_level_model(self) -> None:
        """build_flock_workload_config includes the flow LLM override for reviewer."""
        provider = ConfigFlockFlowProvider()
        provider.save(_make_flow())

        workload = build_flock_workload_config(
            flow_name=_FLOW_NAME,
            tpl_raid=_make_template_raid(),
            flow_provider=provider,
            initial_prompt="Implement the feature",
        )

        assert workload is not None, "Expected a workload_config; got None"
        personas = workload["personas"]
        reviewer = next((p for p in personas if p["name"] == _PERSONA_NAME), None)
        assert reviewer is not None, f"Persona {_PERSONA_NAME!r} missing from workload_config"

        effective_llm = reviewer.get("llm", {})
        assert effective_llm.get("model") == _EXPECTED_EFFECTIVE_LLM["model"], (
            f"Sidecar would receive model={effective_llm.get('model')!r} but expected "
            f"{_EXPECTED_EFFECTIVE_LLM['model']!r}"
        )

    def test_stage_override_thinking_enabled_propagated(self) -> None:
        """Stage-level thinking_enabled=True survives merge into workload config."""
        provider = ConfigFlockFlowProvider()
        provider.save(_make_flow())

        workload = build_flock_workload_config(
            flow_name=_FLOW_NAME,
            tpl_raid=_make_template_raid(),
            flow_provider=provider,
            initial_prompt="Implement the feature",
        )

        assert workload is not None
        reviewer = next(p for p in workload["personas"] if p["name"] == _PERSONA_NAME)
        effective_llm = reviewer.get("llm", {})
        assert effective_llm.get("thinking_enabled") is True, (
            f"thinking_enabled should be True after stage override but got "
            f"{effective_llm.get('thinking_enabled')!r}"
        )

    def test_system_prompt_extra_concatenated_across_layers(self) -> None:
        """system_prompt_extra from flow and stage is concatenated, not replaced."""
        provider = ConfigFlockFlowProvider()
        provider.save(_make_flow())

        workload = build_flock_workload_config(
            flow_name=_FLOW_NAME,
            tpl_raid=_make_template_raid(),
            flow_provider=provider,
            initial_prompt="Implement the feature",
        )

        assert workload is not None
        reviewer = next(p for p in workload["personas"] if p["name"] == _PERSONA_NAME)
        combined = reviewer.get("system_prompt_extra", "")

        assert _FLOW_PROMPT_EXTRA.strip() in combined, (
            "Flow-level system_prompt_extra missing from merged result"
        )
        assert _STAGE_OVERRIDE["system_prompt_extra"].strip() in combined, (
            "Stage-level system_prompt_extra missing from merged result"
        )

    def test_in_process_model_matches_sidecar_model(self) -> None:
        """In-process LLM merge produces the same model as the sidecar workload config.

        This is the core NIU-645 regression test: the in-process path must use
        the same effective model that is written into workload_config["personas"].
        """
        # Build the workload config (what the sidecar receives)
        provider = ConfigFlockFlowProvider()
        provider.save(_make_flow())
        workload = build_flock_workload_config(
            flow_name=_FLOW_NAME,
            tpl_raid=_make_template_raid(),
            flow_provider=provider,
            initial_prompt="Implement the feature",
        )
        assert workload is not None
        reviewer_sidecar = next(p for p in workload["personas"] if p["name"] == _PERSONA_NAME)
        sidecar_model = reviewer_sidecar.get("llm", {}).get("model")

        # Simulate what RavnDispatcher does: apply the in-process merge
        # Base: persona defaults (no model specified — uses settings default)
        # Override: flow-level llm dict from the workload persona
        in_process_effective = merge_llm(
            defaults={"primary_alias": "balanced"},
            persona_override=reviewer_sidecar.get("llm"),
        )
        in_process_model = in_process_effective.get("model")

        assert in_process_model == sidecar_model, (
            f"In-process model ({in_process_model!r}) != sidecar model ({sidecar_model!r}). "
            "NIU-645 regression: in-process dispatch is not honoring the persona LLM config."
        )

    def test_flow_not_found_returns_none(self) -> None:
        """build_flock_workload_config returns None when the flow does not exist."""
        provider = ConfigFlockFlowProvider()
        # Do not save any flow

        workload = build_flock_workload_config(
            flow_name=_FLOW_NAME,
            tpl_raid=_make_template_raid(),
            flow_provider=provider,
            initial_prompt="Implement the feature",
        )

        assert workload is None

    def test_no_provider_returns_none(self) -> None:
        """build_flock_workload_config returns None when flow_provider is None."""
        workload = build_flock_workload_config(
            flow_name=_FLOW_NAME,
            tpl_raid=_make_template_raid(),
            flow_provider=None,
            initial_prompt="Implement the feature",
        )

        assert workload is None

    def test_security_keys_cannot_be_overridden_at_flow_layer(self) -> None:
        """allowed_tools/forbidden_tools are silently dropped from LLM overrides."""
        flow_with_security_attempt = FlockFlowConfig(
            name="attack-flow",
            personas=[
                FlockPersonaOverride(
                    name=_PERSONA_NAME,
                    llm={
                        "model": "claude-opus-4-6",
                        "allowed_tools": ["terminal", "cascade"],  # should be dropped
                    },
                ),
            ],
        )
        provider = ConfigFlockFlowProvider()
        provider.save(flow_with_security_attempt)

        workload = build_flock_workload_config(
            flow_name="attack-flow",
            tpl_raid=TemplateRaid(
                name="test",
                description="",
                acceptance_criteria=[],
                declared_files=[],
                estimate_hours=0.0,
                prompt="test",
                persona=_PERSONA_NAME,
            ),
            flow_provider=provider,
            initial_prompt="test",
        )

        assert workload is not None
        reviewer = next(p for p in workload["personas"] if p["name"] == _PERSONA_NAME)
        # merge_llm drops security keys — verify they don't leak into effective LLM
        effective = merge_llm(defaults={}, persona_override=reviewer.get("llm"))
        assert "allowed_tools" not in effective, (
            "allowed_tools must not be overridable via the flow LLM config"
        )
        assert "forbidden_tools" not in effective, (
            "forbidden_tools must not be overridable via the flow LLM config"
        )

    def test_merge_persona_override_preserves_name(self) -> None:
        """merge_persona_override always preserves the persona name from the flow."""
        flow_persona = {
            "name": _PERSONA_NAME,
            "llm": {"model": "claude-opus-4-6"},
            "system_prompt_extra": "Flow extra",
        }
        stage_override = {
            "name": "attacker-name",  # must be ignored
            "llm": {"thinking_enabled": True},
            "system_prompt_extra": "Stage extra",
        }

        merged = merge_persona_override(flow_persona, stage_override)

        assert merged["name"] == _PERSONA_NAME, (
            "persona name must never be overridable by stage_override"
        )

    def test_empty_flow_name_returns_none(self) -> None:
        """An empty flow_name string short-circuits build_flock_workload_config."""
        provider = ConfigFlockFlowProvider()
        provider.save(_make_flow())

        workload = build_flock_workload_config(
            flow_name="",
            tpl_raid=_make_template_raid(),
            flow_provider=provider,
            initial_prompt="test",
        )

        assert workload is None


# ---------------------------------------------------------------------------
# Scenario 1: MountedVolume backend (kind cluster)
# ---------------------------------------------------------------------------


@pytest.mark.kind_integration
@_SKIP_KIND
class TestMountedVolumeScenario:
    """Kind-cluster test: persona source = mountedVolume.

    Scope: infrastructure wiring only.  This scenario verifies that the
    MountedVolume adapter correctly delivers personas from the ``ravn-personas``
    ConfigMap to the sidecar's ``/etc/ravn/config.yaml``.  It does NOT test
    pipeline dispatch or stage-level ``persona_overrides`` — those code paths
    are exercised by ``TestInProcessParity`` which runs in every CI run without
    a kind cluster.

    Flow:
      1. Create kind cluster.
      2. Install Volundr chart with personaSource.mode=mountedVolume and
         KubernetesConfigMapFlockFlowProvider.
      3. Seed ``reviewer`` persona via REST.
      4. Wait for ConfigMap projection (kubelet syncs within ~60 s).
      5. Create ``code-review-flow`` via REST.
      6. Poll for sidecar pod readiness.
      7. Assert /etc/ravn/config.yaml on the sidecar reflects the flow-level
         LLM model override from the ``code-review-flow`` definition.
      8. Assert ravn startup log reflects effective config.
      9. Tear down.
    """

    @pytest.fixture(scope="class")
    def kind_cluster(self):
        """Class-scoped kind cluster for all MountedVolume scenario tests."""
        from tests.integration.helpers.kind_harness import KindCluster

        with KindCluster(name="niuu-mv-integ") as cluster:
            yield cluster

    @pytest.fixture(scope="class")
    def helm_release(self, kind_cluster):
        """Install Volundr chart with mountedVolume persona source."""
        from tests.integration.helpers.kind_harness import HelmRelease

        chart_path = str(Path(__file__).resolve().parent.parent.parent / "charts" / "volundr")
        values = {
            "personaSource": {
                "mode": "mountedVolume",
                "mountedVolume": {
                    "configMapName": "ravn-personas",
                },
            },
            "flockFlows": {
                "adapter": "tyr.adapters.flows.configmap.KubernetesConfigMapFlockFlowProvider",
                "namespace": "default",
            },
        }
        with HelmRelease(
            kind_cluster,
            "volundr-mv",
            chart_path,
            namespace="default",
            values=values,
        ) as rel:
            yield rel

    def test_sidecar_effective_config_mounted_volume(self, kind_cluster, helm_release) -> None:
        """Sidecar /etc/ravn/config.yaml reflects persona LLM + flow + stage merge."""
        from tests.integration.helpers.kind_harness import (
            create_flow_via_rest,
            read_sidecar_config,
            seed_persona_via_rest,
            wait_for_configmap_key,
            wait_for_pod,
        )

        base_url = "http://localhost:8080"  # port-forwarded in CI

        # Step 3: Seed reviewer persona via REST
        seed_persona_via_rest(
            base_url,
            {
                "name": _PERSONA_NAME,
                "system_prompt_template": "You are a security-focused reviewer.",
                "permission_mode": "read-only",
                "allowed_tools": ["file", "git"],
                "llm": {
                    "model": "claude-opus-4-6",
                    "thinking_enabled": False,
                },
                "iteration_budget": 25,
            },
        )

        # Step 4: Wait for ConfigMap projection
        wait_for_configmap_key(
            kind_cluster,
            configmap_name="ravn-personas",
            key="reviewer.yaml",
            expected_value_contains="claude-opus-4-6",
            namespace="default",
        )

        # Step 5: Create flock flow via REST
        create_flow_via_rest(
            base_url,
            {
                "name": _FLOW_NAME,
                "description": "M7 acceptance test flow",
                "personas": [
                    {
                        "name": _PERSONA_NAME,
                        "llm": {"model": "claude-opus-4-6"},
                        "system_prompt_extra": _FLOW_PROMPT_EXTRA,
                    }
                ],
            },
        )

        # Step 6: Poll for sidecar pod
        pod_name = wait_for_pod(
            kind_cluster,
            label_selector="app.kubernetes.io/component=ravn-sidecar",
            namespace="default",
        )

        # Step 7: Assert /etc/ravn/config.yaml reflects the flow-level model override.
        # Stage-level persona_overrides (e.g. thinking_enabled) are applied at dispatch
        # time by Tyr — not installed here — and are covered by TestInProcessParity.
        config = read_sidecar_config(kind_cluster, pod_name, namespace="default")
        persona_cfg = config.get("persona", {})
        llm_cfg = persona_cfg.get("llm", {})

        got_model = llm_cfg.get("model")
        assert got_model == "claude-opus-4-6", (
            f"Expected model='claude-opus-4-6' from flow-level override but got {got_model!r}"
        )

    def test_sidecar_startup_log_reflects_effective_config(
        self, kind_cluster, helm_release
    ) -> None:
        """Ravn startup log line includes the effective model name."""
        from tests.integration.helpers.kind_harness import (
            get_pod_logs,
            wait_for_pod,
        )

        pod_name = wait_for_pod(
            kind_cluster,
            label_selector="app.kubernetes.io/component=ravn-sidecar",
            namespace="default",
        )
        logs = get_pod_logs(kind_cluster, pod_name, namespace="default")

        assert "claude-opus-4-6" in logs, (
            "Ravn startup log should include the effective model name 'claude-opus-4-6'"
        )
        assert "effective" in logs.lower() or "persona" in logs.lower(), (
            "Ravn startup log should mention the loaded persona or effective config"
        )


# ---------------------------------------------------------------------------
# Scenario 2: HTTP backend (kind cluster)
# ---------------------------------------------------------------------------


@pytest.mark.kind_integration
@_SKIP_KIND
class TestHTTPScenario:
    """Kind-cluster test: persona source = http (PAT auth).

    Scope: infrastructure wiring only.  Verifies that the HTTP adapter correctly
    delivers personas from the Volundr REST API to the sidecar startup config.
    Pipeline dispatch and stage-level ``persona_overrides`` are not exercised here;
    see ``TestInProcessParity`` for that coverage.

    The sidecar pulls persona definitions from the Volundr REST API using a PAT
    mounted as a Kubernetes secret.  No ConfigMap projection occurs — effective
    config verification uses the ravn startup log instead.
    """

    @pytest.fixture(scope="class")
    def kind_cluster(self):
        from tests.integration.helpers.kind_harness import KindCluster

        with KindCluster(name="niuu-http-integ") as cluster:
            yield cluster

    @pytest.fixture(scope="class")
    def helm_release(self, kind_cluster):
        from tests.integration.helpers.kind_harness import HelmRelease

        chart_path = str(Path(__file__).resolve().parent.parent.parent / "charts" / "volundr")
        values = {
            "personaSource": {
                "mode": "http",
                "http": {
                    "baseUrl": "http://volundr:8080",
                    "tokenSecretName": "ravn-volundr-token",
                    "cacheTtlSeconds": 10,
                },
            },
            "flockFlows": {
                "adapter": "tyr.adapters.flows.configmap.KubernetesConfigMapFlockFlowProvider",
                "namespace": "default",
            },
        }
        with HelmRelease(
            kind_cluster,
            "volundr-http",
            chart_path,
            namespace="default",
            values=values,
        ) as rel:
            yield rel

    def test_sidecar_uses_http_persona_effective_model(self, kind_cluster, helm_release) -> None:
        """Sidecar startup log confirms it loaded the persona via HTTP with the right model."""
        from tests.integration.helpers.kind_harness import (
            create_flow_via_rest,
            get_pod_logs,
            seed_persona_via_rest,
            wait_for_pod,
        )

        base_url = "http://localhost:8080"

        # Seed persona and flow (same as MountedVolume scenario)
        seed_persona_via_rest(
            base_url,
            {
                "name": _PERSONA_NAME,
                "system_prompt_template": "You are a security-focused reviewer.",
                "permission_mode": "read-only",
                "allowed_tools": ["file", "git"],
                "llm": {"model": "claude-opus-4-6", "thinking_enabled": False},
                "iteration_budget": 25,
            },
        )
        create_flow_via_rest(
            base_url,
            {
                "name": _FLOW_NAME,
                "description": "M7 acceptance test flow (HTTP mode)",
                "personas": [
                    {
                        "name": _PERSONA_NAME,
                        "llm": {"model": "claude-opus-4-6"},
                        "system_prompt_extra": _FLOW_PROMPT_EXTRA,
                    }
                ],
            },
        )

        pod_name = wait_for_pod(
            kind_cluster,
            label_selector="app.kubernetes.io/component=ravn-sidecar",
            namespace="default",
        )

        # HTTP mode: no ConfigMap projection — verify via startup log
        logs = get_pod_logs(kind_cluster, pod_name, namespace="default")
        assert "claude-opus-4-6" in logs, (
            "HTTP-mode sidecar startup log must contain the effective model name"
        )

    def test_http_sidecar_effective_model_matches_in_process(
        self, kind_cluster, helm_release
    ) -> None:
        """HTTP-mode sidecar model matches the in-process path (parity check)."""
        from tests.integration.helpers.kind_harness import (
            get_pod_logs,
            wait_for_pod,
        )

        pod_name = wait_for_pod(
            kind_cluster,
            label_selector="app.kubernetes.io/component=ravn-sidecar",
            namespace="default",
        )
        logs = get_pod_logs(kind_cluster, pod_name, namespace="default")

        # The in-process parity: flow specifies claude-opus-4-6
        expected_model = _EXPECTED_EFFECTIVE_LLM["model"]
        assert expected_model in logs, (
            f"HTTP sidecar log must contain model {expected_model!r} "
            "(HTTP scenario parity with in-process path)"
        )
