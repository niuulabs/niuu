"""One rule for whether this resident's backend can load a learned tool.

The live defect: on a ``k8s_job`` resident, ``capability_list`` advertised
tools that import the host SDK, ``learned_tool_run`` refused them at load, and
the resident rediscovered that on every turn while curation counted them kept.
The loader, the catalog, curation and build_tool must all ask the same
question; these tests pin that they do and that a refusal is never silent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ravn.adapters.tools.capability_catalog import CapabilityListTool
from ravn.cap_telemetry import degraded_counts, reset_cap_hits
from ravn.valkyrie_evolution.learned_tools import (
    LearnedTool,
    LearnedToolError,
    LearnedToolResolver,
    LearnedToolUnloadableError,
    RefusedLearnedTool,
    backend_load_refusal,
    learned_tool_storage,
    require_backend_support,
    write_learned_tool,
    write_learned_tool_artifact,
)
from ravn.valkyrie_evolution.models import (
    LearnedToolArtifact,
    LearnedToolManifest,
    ToolReachGrant,
)

_SELF_CONTAINED = "def run(payload):\n    return {'ok': True}\n"
_HOST_SDK = "from ravn.sdk import tool\n\ndef run(payload):\n    return tool.kubernetes_inspect()\n"
_DIGEST = "ghcr.io/niuulabs/devrunner@sha256:" + "a" * 64

_K8S_KWARGS: dict[str, Any] = {
    "namespace": "tools",
    "deny_policy_name": "deny",
    "allow_policy_name": "allow",
    "image": _DIGEST,
}


class _Runner:
    """A backend double: configurable host-call support and reach refusal."""

    def __init__(self, *, host_call: bool, reach_error: str = "") -> None:
        self.supports_host_call = host_call
        self._reach_error = reach_error

    def check_reach(self, declared_reach: Any) -> None:
        if self._reach_error:
            raise LearnedToolError(self._reach_error)


def _manifest(name: str = "a_tool", reach: list[ToolReachGrant] | None = None) -> Any:
    return LearnedToolManifest(
        name=name,
        description=f"Tool {name}.",
        input_schema={"type": "object"},
        required_permission="tool:run",
        declared_reach=reach or [],
    )


def _install(
    state: Path,
    name: str,
    *,
    code: str = _SELF_CONTAINED,
    verified: bool = True,
    reach: list[ToolReachGrant] | None = None,
) -> LearnedToolArtifact:
    artifact = LearnedToolArtifact(
        artifact_id=f"learned-tool:{name}",
        manifest=_manifest(name, reach),
        tool_code=code,
        provenance={"verification": {"ok": True, "logs": "fixture"}} if verified else {},
    )
    code_dir, artifacts_dir = learned_tool_storage(state)
    write_learned_tool(tools_dir=code_dir, artifact=artifact)
    write_learned_tool_artifact(artifacts_dir=artifacts_dir, artifact=artifact)
    return artifact


class TestTheRule:
    def test_a_host_sdk_tool_needs_a_backend_that_can_call_back(self) -> None:
        manifest = _manifest()

        require_backend_support(
            manifest=manifest, tool_code=_HOST_SDK, runner=_Runner(host_call=True)
        )
        with pytest.raises(LearnedToolUnloadableError, match="rebuild it self-contained"):
            require_backend_support(
                manifest=manifest, tool_code=_HOST_SDK, runner=_Runner(host_call=False)
            )

    def test_a_self_contained_tool_loads_anywhere(self) -> None:
        assert (
            backend_load_refusal(
                manifest=_manifest(), tool_code=_SELF_CONTAINED, runner=_Runner(host_call=False)
            )
            is None
        )

    def test_reach_the_backend_cannot_enforce_is_a_refusal_too(self) -> None:
        reason = backend_load_refusal(
            manifest=_manifest(reach=[ToolReachGrant(kind="kubernetes", access="read")]),
            tool_code=_SELF_CONTAINED,
            runner=_Runner(host_call=False, reach_error="no read-only tool identity configured"),
        )

        assert reason == "no read-only tool identity configured"

    def test_the_loader_applies_the_same_rule(self, tmp_path: Path) -> None:
        path = tmp_path / "a_tool.py"
        path.write_text(_HOST_SDK, encoding="utf-8")

        with pytest.raises(LearnedToolUnloadableError, match="ravn.sdk"):
            LearnedTool(manifest=_manifest(), tool_path=path, runner=_Runner(host_call=False))  # type: ignore[arg-type]
        LearnedTool(manifest=_manifest(), tool_path=path, runner=_Runner(host_call=True))  # type: ignore[arg-type]


class TestResolver:
    def test_loadable_and_refused_are_split_with_the_loaders_reasons(self, tmp_path: Path) -> None:
        _install(tmp_path, "self_contained")
        _install(tmp_path, "uses_host_sdk", code=_HOST_SDK)
        _install(tmp_path, "unverified", verified=False)
        resolver = LearnedToolResolver(state_dir=tmp_path, execution_backend="container")

        inventory = resolver.inventory()

        assert [a.manifest.name for a in inventory.loadable] == ["self_contained"]
        refused = {item.name: item.reason for item in inventory.refused}
        assert set(refused) == {"uses_host_sdk", "unverified"}
        assert "no verification record" in refused["unverified"]
        for name, reason in refused.items():
            with pytest.raises(LearnedToolError) as loader:
                resolver.load(name)
            assert reason == str(loader.value)

    def test_reach_refusal_comes_from_the_backend_itself(self, tmp_path: Path) -> None:
        _install(
            tmp_path,
            "reads_the_cluster",
            reach=[ToolReachGrant(kind="kubernetes", target="pods", access="read")],
        )
        resolver = LearnedToolResolver(
            state_dir=tmp_path,
            execution_backend="k8s_job",
            execution_backend_kwargs=_K8S_KWARGS,
        )

        (refused,) = resolver.list_refused()

        assert "tool_service_account" in refused.reason
        assert resolver.list_loadable_artifacts() == []

    def test_every_refusal_is_reported_loudly_once(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        reset_cap_hits()
        _install(tmp_path, "uses_host_sdk", code=_HOST_SDK)
        resolver = LearnedToolResolver(
            state_dir=tmp_path, execution_backend="k8s_job", execution_backend_kwargs=_K8S_KWARGS
        )

        with caplog.at_level("ERROR"):
            resolver.list_loadable_artifacts()
            resolver.list_loadable_artifacts()

        assert degraded_counts() == {"learned_tool.refused": 1}
        (record,) = [r for r in caplog.records if "degraded [learned_tool.refused]" in r.message]
        assert record.levelname == "ERROR"
        assert "uses_host_sdk" in record.message
        assert "build_tool" in record.message
        reset_cap_hits()

    def test_an_unbuildable_backend_stops_construction_with_the_remedy(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(LearnedToolError, match="learned_tool_execution_backend"):
            LearnedToolResolver(state_dir=tmp_path, execution_backend="qemu")
        with pytest.raises(LearnedToolError, match="explicit namespace"):
            LearnedToolResolver(
                state_dir=tmp_path,
                execution_backend="k8s_job",
                execution_backend_kwargs={**_K8S_KWARGS, "namespace": ""},
            )


class TestCatalog:
    async def test_refused_tools_are_named_apart_from_the_callable_catalog(
        self, tmp_path: Path
    ) -> None:
        _install(tmp_path, "self_contained")
        _install(tmp_path, "uses_host_sdk", code=_HOST_SDK)
        resolver = LearnedToolResolver(state_dir=tmp_path, execution_backend="container")
        catalog = CapabilityListTool(
            tools_provider=lambda: [],
            learned_tools_provider=resolver.list_loadable_artifacts,
            refused_learned_tools=resolver.list_refused,
        )

        listed = json.loads((await catalog.execute({})).content)
        detail = json.loads((await catalog.execute({"names": ["uses_host_sdk"]})).content)

        assert [c["name"] for c in listed["capabilities"]] == ["self_contained"]
        assert listed["unavailable_learned_tools"] == ["uses_host_sdk"]
        assert "unavailable_matches" not in listed
        assert detail["capabilities"] == []
        assert detail["not_found"] == ["uses_host_sdk"]
        (match,) = detail["unavailable_matches"]
        assert "ravn.sdk" in match["reason"]

    async def test_nothing_refused_adds_nothing_to_the_payload(self) -> None:
        catalog = CapabilityListTool(
            tools_provider=lambda: [],
            learned_tools_provider=lambda: [],
            refused_learned_tools=lambda: [],
        )

        payload = json.loads((await catalog.execute({})).content)

        assert "unavailable_learned_tools" not in payload

    def test_a_refused_tool_wraps_its_artifact_and_reason(self) -> None:
        artifact = LearnedToolArtifact(artifact_id="x", manifest=_manifest("x"), tool_code="")

        assert RefusedLearnedTool(artifact=artifact, reason="because").name == "x"
