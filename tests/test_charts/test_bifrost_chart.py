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
