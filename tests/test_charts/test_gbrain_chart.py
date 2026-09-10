"""Render real gbrain workloads, including failure cases for unsafe combinations."""

import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "charts" / "gbrain"


def render(*extra: str):
    return subprocess.run(
        [
            "helm",
            "template",
            "brain",
            str(CHART),
            "--set",
            "image.repository=test/gbrain",
            "--set",
            "existingSecret=brain-credentials",
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def test_persistent_server_and_secret_reference():
    result = render()
    assert result.returncode == 0, result.stderr
    docs = {d["kind"]: d for d in yaml.safe_load_all(result.stdout)}
    pod = docs["Deployment"]["spec"]["template"]["spec"]
    assert docs["Deployment"]["spec"]["strategy"]["type"] == "Recreate"
    assert (
        docs["PersistentVolumeClaim"]["metadata"]["annotations"]["helm.sh/resource-policy"]
        == "keep"
    )
    assert (
        pod["containers"][0]["env"][1]["valueFrom"]["secretKeyRef"]["key"]
        == "GBRAIN_ADMIN_BOOTSTRAP_TOKEN"
    )
    assert "CronJob" not in docs
    assert "--no-embedding" in docs["ConfigMap"]["data"]["start.sh"]


def test_postgres_maintenance_has_no_access_to_live_pglite_files():
    result = render("--set", "engine=postgres", "--set", "dream.enabled=true")
    assert result.returncode == 0, result.stderr
    docs = {d["kind"]: d for d in yaml.safe_load_all(result.stdout)}
    job = docs["CronJob"]["spec"]
    assert job["concurrencyPolicy"] == "Forbid"
    pod = job["jobTemplate"]["spec"]["template"]["spec"]
    assert all("persistentVolumeClaim" not in volume for volume in pod["volumes"])
    assert "--phase" in pod["containers"][0]["args"][0]
    assert "GBRAIN_DATABASE_URL" in docs["ConfigMap"]["data"]["start.sh"]


@pytest.mark.parametrize(
    "args, message",
    [
        (["--set", "dream.enabled=true"], "Scheduled dreams require engine=postgres"),
        (["--set", "postgres.enabled=true"], "postgres.enabled requires engine=postgres"),
        (["--set", "engine=invalid"], "engine must be pglite or postgres"),
        (["--set", "embedding.enabled=true"], "embedding.model is required"),
    ],
)
def test_invalid_configuration_fails(args, message):
    result = render(*args)
    assert result.returncode != 0
    assert message in result.stderr


def test_native_dream_jobs_are_separate_from_service():
    result = render(
        "--set",
        "engine=postgres",
        "--set",
        "dream.enabled=true",
    )
    assert result.returncode == 0, result.stderr
    docs = list(yaml.safe_load_all(result.stdout))
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    assert [c["name"] for c in pod["containers"]] == ["gbrain"]
    config = next(d for d in docs if d["kind"] == "ConfigMap" and "start.sh" in d["data"])
    assert "--non-interactive" in config["data"]["start.sh"]
    dream = next(d for d in docs if d["kind"] == "CronJob")
    labels = dream["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"]
    assert labels["app.kubernetes.io/component"] == "dream"
    assert "app.kubernetes.io/name" not in labels  # Never selected by the HTTP service.


def test_gbrain_rejects_warden():
    result = render("--set", "warden.enabled=true")
    assert result.returncode != 0
    assert "gbrain uses native dream cycles" in result.stderr


def test_managed_postgres_uses_operator_credentials_and_configured_storage():
    result = render(
        "--set",
        "engine=postgres",
        "--set",
        "postgres.enabled=true",
        "--set",
        "existingSecret=",
        "--set",
        "dream.enabled=true",
        "--set",
        "postgres.storageClass=harvester-data",
        "--set",
        "persistence.storageClass=harvester-data",
    )
    assert result.returncode == 0, result.stderr
    docs = {d["kind"]: d for d in yaml.safe_load_all(result.stdout)}
    db = docs["Cluster"]["spec"]
    assert db["storage"] == {"storageClass": "harvester-data", "size": "5Gi"}
    assert db["managed"]["roles"] == [{"name": "gbrain", "login": True, "bypassrls": True}]
    assert "secret" not in db["bootstrap"]["initdb"]
    assert docs["PersistentVolumeClaim"]["spec"]["storageClassName"] == "harvester-data"
    assert docs["Secret"]["metadata"]["name"] == "brain-gbrain-admin"
    containers = [
        docs["Deployment"]["spec"]["template"]["spec"]["containers"][0],
        docs["CronJob"]["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0],
    ]
    for container in containers:
        env = next(e for e in container["env"] if e["name"] == "GBRAIN_DATABASE_URL")
        assert env["valueFrom"]["secretKeyRef"] == {"name": "brain-gbrain-db-app", "key": "uri"}
