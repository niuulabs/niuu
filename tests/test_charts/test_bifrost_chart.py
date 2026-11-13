"""Render real Helm templates to verify embedded Switchyard configuration."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from bifrost.config import BifrostConfig

CHART = Path(__file__).parents[2] / "charts" / "bifrost"


def test_selection_is_opt_in_and_config_is_not_duplicated():
    source = (CHART / "values.yaml").read_text()
    assert source.splitlines().count("config:") == 1
    assert yaml.safe_load(source)["config"]["selection"] is None


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required to render charts")
@pytest.mark.parametrize("classifier", [False, True])
def test_switchyard_config_reaches_runtime_and_triggers_rollout(tmp_path, classifier):
    values = yaml.safe_load((CHART / "values-switchyard-nemotron.yaml").read_text())
    selection = values["config"]["selection"]
    if classifier:
        # Explicit second-model fixture; not an assertion of live model availability.
        selection["targets"]["second"] = {
            "provider": "valaskjalf",
            "model": "test-second-model",
            "weight": 3,
        }
        values["config"]["providers"]["valaskjalf"]["models"].append("test-second-model")
        selection["routes"]["local"].update(
            targets=["nemotron", "second"],
            judge="nemotron",
            prompt="Return JSON with target equal to nemotron or second.",
            max_tokens=128,
        )
    override = tmp_path / "values.yaml"
    override.write_text(yaml.safe_dump(values))

    def render(*extra):
        output = subprocess.check_output(
            ["helm", "template", "bifrost", str(CHART), *extra], text=True
        )
        resources = list(yaml.safe_load_all(output))
        deployment = next(r for r in resources if r["kind"] == "Deployment")
        configmap = next(r for r in resources if r["kind"] == "ConfigMap")
        return deployment["spec"]["template"], configmap

    default_pod, _ = render()
    pod, configmap = render("-f", str(override), "--set-string", "image.tag=test-switchyard")
    parsed = BifrostConfig.model_validate(yaml.safe_load(configmap["data"]["bifrost.yaml"]))
    assert parsed.selection == selection
    assert (
        parsed.providers["valaskjalf"].models
        == values["config"]["providers"]["valaskjalf"]["models"]
    )
    assert (
        pod["metadata"]["annotations"]["checksum/config"]
        != default_pod["metadata"]["annotations"]["checksum/config"]
    )
    container = pod["spec"]["containers"][0]
    assert container["image"] == "ghcr.io/niuulabs/niuu:test-switchyard"
    assert container["command"] == ["python", "-m", "bifrost"]
    assert container["args"] == ["--config", "/etc/bifrost/bifrost.yaml"]
    assert {"name": "config", "mountPath": "/etc/bifrost", "readOnly": True} in container[
        "volumeMounts"
    ]
    assert {"name": "config", "configMap": {"name": configmap["metadata"]["name"]}} in pod["spec"][
        "volumes"
    ]


# ---------------------------------------------------------------------------
# Rolling the gateway must not cut a streamed model answer that is still running
# ---------------------------------------------------------------------------


def _values() -> dict:
    return yaml.safe_load((CHART / "values.yaml").read_text())


def _render_deployment(*extra: str) -> dict:
    output = subprocess.check_output(["helm", "template", "bifrost", str(CHART), *extra], text=True)
    resources = list(yaml.safe_load_all(output))
    return next(r for r in resources if r["kind"] == "Deployment")["spec"]["template"]["spec"]


def test_the_default_grace_period_covers_the_drain_and_matches_the_runtime_default():
    values = _values()
    drain = values["shutdown"]["drainSeconds"]
    grace = values["config"]["shutdown_grace_seconds"]

    assert values["terminationGracePeriodSeconds"] > drain + grace
    # The chart and the runtime agree on how long in-flight streams get.
    assert grace == BifrostConfig().shutdown_grace_seconds


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required to render charts")
class TestRolloutDrainsInFlightStreams:
    def test_the_gateway_keeps_serving_while_endpoints_are_withdrawn_then_drains(self):
        pod = _render_deployment()

        values = _values()
        gateway = next(c for c in pod["containers"] if c["name"] == "bifrost")
        assert gateway["lifecycle"] == {"preStop": {"sleep": {"seconds": 10}}}
        # The kubelet waits for the preStop sleep AND the process's own drain.
        assert pod["terminationGracePeriodSeconds"] == values["terminationGracePeriodSeconds"]
        assert (
            pod["terminationGracePeriodSeconds"] > 10 + values["config"]["shutdown_grace_seconds"]
        )

    def test_the_grace_period_reaches_the_runtime_through_the_config_file(self):
        output = subprocess.check_output(
            [
                "helm",
                "template",
                "bifrost",
                str(CHART),
                "--set",
                "config.shutdown_grace_seconds=120",
                "--set",
                "terminationGracePeriodSeconds=200",
            ],
            text=True,
        )
        configmap = next(
            r
            for r in yaml.safe_load_all(output)
            if r["kind"] == "ConfigMap" and "bifrost.yaml" in (r.get("data") or {})
        )

        parsed = BifrostConfig.model_validate(yaml.safe_load(configmap["data"]["bifrost.yaml"]))

        assert parsed.shutdown_grace_seconds == 120

    def test_envoy_outlives_the_gateways_drain_or_it_would_cut_the_streams_itself(self):
        pod = _render_deployment("--set", "envoy.enabled=true")

        drains = {c["name"]: c.get("lifecycle") for c in pod["containers"]}
        assert drains["bifrost"] == {"preStop": {"sleep": {"seconds": 10}}}
        assert drains["envoy"] == {"preStop": {"sleep": {"seconds": 10 + 300}}}

    def test_the_sleep_can_be_turned_off(self):
        pod = _render_deployment("--set", "shutdown.drainSeconds=0", "--set", "envoy.enabled=true")

        containers = {c["name"]: c for c in pod["containers"]}
        assert "lifecycle" not in containers["bifrost"]
        # Envoy still has to outlive the gateway's in-flight drain.
        assert containers["envoy"]["lifecycle"] == {"preStop": {"sleep": {"seconds": 300}}}

    def test_a_grace_period_shorter_than_the_drain_is_a_render_error_not_a_silent_kill(self):
        result = subprocess.run(
            [
                "helm",
                "template",
                "bifrost",
                str(CHART),
                "--set",
                "terminationGracePeriodSeconds=30",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "must exceed shutdown.drainSeconds + config.shutdown_grace_seconds" in result.stderr
        assert "SIGKILL" in result.stderr
