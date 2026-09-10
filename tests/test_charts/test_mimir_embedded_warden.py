"""Embedded wardens use the same runtime settings as manual wardens."""

import subprocess
from pathlib import Path

import yaml

from ravn.cli.embedded_warden import prepare_runtime


def test_chart_binds_custom_storage_and_manual_warden_profile(tmp_path):
    values = tmp_path / "values.yaml"
    values.write_text(
        yaml.safe_dump(
            {
                "config": {"name": "shared", "dataPath": str(tmp_path / "wiki")},
                "warden": {
                    "enabled": True,
                    "image": "test/ravn:1",
                    "spec": {
                        "model": "claude-sonnet-4-6",
                        "persona": "mimir-warden",
                        "profile": "shared",
                        "features": {"recap_enabled": False},
                        "schedules": {
                            "dream_cycle_cron_expression": "0 4 * * *",
                            "staleness_trigger_schedule_hours": 12,
                        },
                    },
                    "config": {"logging": {"level": "debug"}},
                    "envFrom": [{"secretRef": {"name": "warden-provider"}}],
                },
            }
        )
    )
    chart = Path(__file__).resolve().parents[2] / "charts/mimir"
    rendered = subprocess.run(
        ["helm", "template", "release", str(chart), "-f", str(values)],
        capture_output=True,
        text=True,
        check=True,
    )
    docs = list(yaml.safe_load_all(rendered.stdout))
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    warden = next(c for c in pod["containers"] if c["name"] == "warden")
    assert warden["command"] == ["python", "-m", "ravn.cli.embedded_warden"]
    assert warden["args"][-1] == str(tmp_path / "wiki")
    assert warden["volumeMounts"][0]["mountPath"] == str(tmp_path / "wiki")
    assert warden["envFrom"] == [{"secretRef": {"name": "warden-provider"}}]
    assert deployment["metadata"]["labels"]["niuu.world/warden-id"] == "shared-warden"
    cm = next(d for d in docs if d["kind"] == "ConfigMap" and "spec.yaml" in d.get("data", {}))
    spec, config = prepare_runtime(
        yaml.safe_load(cm["data"]["spec.yaml"]), name="shared", data_path=tmp_path / "wiki"
    )
    settings = yaml.safe_load(config.read_text())
    assert spec.profile == "shared"
    assert settings["dream_cycle"]["cron_expression"] == "0 4 * * *"
    assert settings["mimir"]["write_routing"]["default"] == ["shared"]
    assert settings["mimir"]["staleness_trigger"]["schedule_hours"] == 12
    assert settings["mimir"]["source_trigger"]["enabled"]
    assert not settings["recap"]["enabled"]
    assert settings["logging"]["level"] == "debug"
    assert settings["gateway"]["channels"]["http"]["port"] == 8764
    assert str(tmp_path / "wiki/.warden") in settings["initiative"]["queue_journal_path"]
    assert settings["mcp_servers"][0]["args"][3] == "--adapter-config"
    assert list((tmp_path / "wiki/.warden").glob("mount-*.yaml"))
    assert config.stat().st_mode & 0o777 == 0o600
