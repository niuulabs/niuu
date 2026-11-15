"""The Mímir chart renders the memory gateway from named values."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from mimir.config import MimirServiceConfig

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="Helm is required to render charts"
)

_CHART = Path(__file__).resolve().parents[3] / "charts/mimir"
_BACKEND = {"mcpUrl": "http://gbrain:3131/mcp", "apiTokenSecret": {"name": "gbrain-token"}}
_SYSTEM = {
    "name": "acme-brain",
    "mcpUrl": "http://acme-brain:3131/mcp",
    "adminTokenSecret": {"name": "acme-brain-admin", "key": "token"},
    "signingKeySecret": {"name": "acme-memory-seal", "key": "key"},
}


_STEP2 = {
    "serviceAuth": {"adapter": "niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter"},
}


def _helm(tmp_path: Path, memory: dict) -> subprocess.CompletedProcess[str]:
    values = tmp_path / "values.yaml"
    values.write_text(yaml.safe_dump({"config": {"backend": _BACKEND, "memory": memory}}))
    return subprocess.run(
        ["helm", "template", "release", str(_CHART), "-f", str(values)],
        capture_output=True,
        text=True,
    )


def _docs(rendered: subprocess.CompletedProcess[str]) -> list[dict]:
    assert rendered.returncode == 0, rendered.stderr
    return [d for d in yaml.safe_load_all(rendered.stdout) if d]


def _config(docs: list[dict]) -> dict:
    cm = next(d for d in docs if d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {}))
    return yaml.safe_load(cm["data"]["config.yaml"])


def test_memory_is_off_by_default(tmp_path: Path) -> None:
    config = _config(_docs(_helm(tmp_path, {})))
    assert "memory" not in config
    assert config["backend"]["mcp_url"] == "http://gbrain:3131/mcp"


def test_an_enabled_gateway_renders_its_systems_tenants_and_secret_mounts(
    tmp_path: Path,
) -> None:
    docs = _docs(
        _helm(
            tmp_path,
            {
                "enabled": True,
                "forgeBaseUrl": "http://volundr:8080",
                "forgeTimeoutSeconds": 5,
                "defaultResultCount": 20,
                "systems": [{**_SYSTEM, "recallLimit": 100, "pageSize": 50}],
                "tenants": {"acme": "acme-brain"},
                **_STEP2,
                "retentionDays": 7,
                "retentionSweepSeconds": 600,
                "orgAdminRoles": ["admin"],
                "connectorServiceSubjects": ["ting"],
            },
        )
    )
    memory = _config(docs)["memory"]
    assert memory["service_auth"] == _STEP2["serviceAuth"]
    assert not {"bifrost_url", "bifrost_timeout_seconds", "ask_enabled"} & set(memory)
    assert (memory["retention_days"], memory["retention_sweep_seconds"]) == (7, 600)
    assert memory["org_admin_roles"] == ["admin"]
    assert memory["connector_service_subjects"] == ["ting"]
    assert memory["forge_base_url"] == "http://volundr:8080"
    assert memory["forge_timeout_seconds"] == 5
    assert memory["default_result_count"] == 20
    assert memory["tenants"] == {"acme": "acme-brain"}
    assert memory["systems"] == [
        {
            "name": "acme-brain",
            "adapter": "mimir.backends.gbrain.GBrainBackend",
            "mcp_url": "http://acme-brain:3131/mcp",
            "admin_token_file": "/etc/mimir-memory/acme-brain/admin-token",
            "signing_key_file": "/etc/mimir-memory/acme-brain/signing-key",
            "page_size": 50,
            "recall_limit": 100,
        }
    ]
    parsed = MimirServiceConfig(memory=memory)
    assert parsed.memory.enabled and parsed.memory.tenants == {"acme": "acme-brain"}
    assert parsed.memory.connector_service_subjects == ["ting"]
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    volume = next(v for v in pod["volumes"] if v["name"] == "memory-system-0")
    secrets = [s["secret"] for s in volume["projected"]["sources"]]
    assert [(s["name"], s["items"][0]["path"]) for s in secrets] == [
        ("acme-brain-admin", "admin-token"),
        ("acme-memory-seal", "signing-key"),
    ]
    mount = next(m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "memory-system-0")
    assert mount == {
        "name": "memory-system-0",
        "mountPath": "/etc/mimir-memory/acme-brain",
        "readOnly": True,
    }


@pytest.mark.parametrize(
    ("memory", "message"),
    [
        ({"enabled": True, "systems": [_SYSTEM]}, "config.memory.forgeBaseUrl is required"),
        (
            {"enabled": True, "forgeBaseUrl": "http://f", "systems": [_SYSTEM]},
            "config.memory.serviceAuth.adapter is required",
        ),
        ({"enabled": True, "forgeBaseUrl": "http://f"}, "at least one memory system"),
        (
            {
                "enabled": True,
                "forgeBaseUrl": "http://f",
                "systems": [{k: v for k, v in _SYSTEM.items() if k != "signingKeySecret"}],
                **_STEP2,
            },
            "signingKeySecret.name is required",
        ),
    ],
)
def test_an_incomplete_gateway_does_not_render(tmp_path: Path, memory, message) -> None:
    rendered = _helm(tmp_path, memory)
    assert rendered.returncode != 0
    assert message in rendered.stderr


def test_the_backend_is_still_required(tmp_path: Path) -> None:
    values = tmp_path / "values.yaml"
    values.write_text(yaml.safe_dump({"config": {"memory": {"enabled": False}}}))
    rendered = subprocess.run(
        ["helm", "template", "release", str(_CHART), "-f", str(values)],
        capture_output=True,
        text=True,
    )
    assert rendered.returncode != 0
    assert "config.backend.mcpUrl is required" in rendered.stderr


def _signing_secrets(docs: list[dict]) -> list[dict]:
    return [
        d
        for d in docs
        if d["kind"] == "Secret" and d["metadata"]["name"] == _SYSTEM["signingKeySecret"]["name"]
    ]


def test_the_chart_creates_a_signing_key_it_is_asked_to_and_keeps_it(tmp_path: Path) -> None:
    system = {**_SYSTEM, "signingKeySecret": {**_SYSTEM["signingKeySecret"], "create": True}}
    memory = {
        "enabled": True,
        "forgeBaseUrl": "http://volundr:8080",
        "systems": [system],
        "tenants": {"acme": "acme-brain"},
        **_STEP2,
    }
    (secret,) = _signing_secrets(_docs(_helm(tmp_path, memory)))
    assert secret["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    assert len(secret["data"]["key"]) >= 64


def test_a_named_signing_key_is_not_created_by_the_chart(tmp_path: Path) -> None:
    memory = {
        "enabled": True,
        "forgeBaseUrl": "http://volundr:8080",
        "systems": [_SYSTEM],
        "tenants": {"acme": "acme-brain"},
        **_STEP2,
    }
    assert _signing_secrets(_docs(_helm(tmp_path, memory))) == []


_PROVISIONING = {
    "enabled": True,
    "namespace": "memory",
    "sourceName": "niuu",
    "sourceNamespace": "knowledge",
    "chartVersion": "2.0.0-dev.610",
    "image": "ghcr.io/niuulabs/gbrain:dev-1@sha256:ab",
    "cluster": "ymir",
    "storageClass": "harvester-data",
    "values": {"postgres": {"instances": 1}},
    "retryAfterSeconds": 60,
    "backend": {"timeout_seconds": 20},
}


def test_provisioning_renders_the_flux_target_its_rbac_and_the_token_mount(
    tmp_path: Path,
) -> None:
    docs = _docs(
        _helm(
            tmp_path,
            {
                "enabled": True,
                "forgeBaseUrl": "http://volundr:8080",
                "provisioning": _PROVISIONING,
                **_STEP2,
            },
        )
    )
    memory = _config(docs)["memory"]
    assert memory["systems"] == []
    assert memory["provisioning"] == {
        "deployment": {
            "adapter": "mimir.adapters.flux.FluxKnowledgeDeploymentAdapter",
            "namespace": "memory",
            "source_name": "niuu",
            "source_namespace": "knowledge",
            "chart_versions": {"gbrain": "2.0.0-dev.610"},
            "images": {"gbrain": "ghcr.io/niuulabs/gbrain:dev-1@sha256:ab"},
            "cluster": "ymir",
            "storage_class": "harvester-data",
            "memory_values": {"postgres": {"instances": 1}},
        },
        "retry_after_seconds": 60,
        "backend": {"timeout_seconds": 20},
    }
    parsed = MimirServiceConfig(memory=memory)
    assert parsed.memory.provisioning.retry_after_seconds == 60
    role = next(d for d in docs if d["kind"] == "Role" and "memory" in d["metadata"]["name"])
    assert role["metadata"]["namespace"] == "memory"
    assert {(tuple(r["resources"]), tuple(r["verbs"])) for r in role["rules"]} == {
        (("helmreleases",), ("get", "list", "create")),
        (("secrets",), ("get", "create")),
        (("services",), ("get",)),
    }
    binding = next(
        d for d in docs if d["kind"] == "RoleBinding" and "memory" in d["metadata"]["name"]
    )
    assert binding["subjects"][0]["name"] == "release-mimir"
    pod = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is True


def test_without_provisioning_no_rbac_and_no_token(tmp_path: Path) -> None:
    docs = _docs(
        _helm(
            tmp_path,
            {"enabled": True, "forgeBaseUrl": "http://f", "systems": [_SYSTEM], **_STEP2},
        )
    )
    assert "provisioning" not in _config(docs)["memory"]
    assert not [d for d in docs if d["kind"] == "Role"]
    pod = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]["spec"]
    assert "automountServiceAccountToken" not in pod


@pytest.mark.parametrize("missing", ["namespace", "sourceName", "chartVersion", "image"])
def test_provisioning_needs_its_target(tmp_path: Path, missing: str) -> None:
    provisioning = {k: v for k, v in _PROVISIONING.items() if k != missing}
    rendered = _helm(
        tmp_path,
        {"enabled": True, "forgeBaseUrl": "http://f", "provisioning": provisioning, **_STEP2},
    )
    assert rendered.returncode != 0
    assert f"config.memory.provisioning.{missing} is required" in rendered.stderr
