"""Tests for the niuu-shared Helm chart templates."""

import re
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
CHART_DIR = REPO_ROOT / "charts" / "niuu-shared"
MIGRATIONS_DIR = REPO_ROOT / "migrations"


def _migration_blocks(template: str) -> dict[str, str]:
    pattern = re.compile(r"^  (\d+_[^:]+\.(?:up|down)\.sql): \|\n", re.MULTILINE)
    matches = list(pattern.finditer(template))
    template_end = template.index("{{- end }}")
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else template_end
        lines = template[start:end].splitlines()
        blocks[name] = (
            "\n".join(line[4:] if line.startswith("    ") else "" for line in lines).rstrip() + "\n"
        )
    return blocks


def test_embeds_valkyrie_history_migration_in_shared_db() -> None:
    template = (CHART_DIR / "templates" / "migrations-configmap.yaml").read_text()
    blocks = _migration_blocks(template)

    for filename in (
        "000049_valkyrie_history.up.sql",
        "000049_valkyrie_history.down.sql",
        "000053_realms_trust_capabilities.up.sql",
        "000053_realms_trust_capabilities.down.sql",
        "000060_credential_enrollments.up.sql",
        "000060_credential_enrollments.down.sql",
    ):
        assert blocks[filename] == (MIGRATIONS_DIR / filename).read_text().rstrip() + "\n"


def test_shared_db_does_not_embed_volundr_session_migrations() -> None:
    template = (CHART_DIR / "templates" / "migrations-configmap.yaml").read_text()
    blocks = _migration_blocks(template)

    assert "000050_session_definition.up.sql" not in blocks
    assert "000050_session_definition.down.sql" not in blocks
    assert "000052_session_activity_state_since.up.sql" not in blocks
    assert "000052_session_activity_state_since.down.sql" not in blocks

    for block in blocks.values():
        assert "ALTER TABLE sessions" not in block


def test_shared_db_does_not_embed_resident_runtimes_migrations() -> None:
    # resident_runtimes (000055) is a volundr-only table; 000079 ALTERs it and
    # would fail against a standalone niuu-shared database that never created it.
    template = (CHART_DIR / "templates" / "migrations-configmap.yaml").read_text()
    blocks = _migration_blocks(template)

    assert "000055_resident_runtimes.up.sql" not in blocks
    assert "000079_resident_realm_binding.up.sql" not in blocks
    for block in blocks.values():
        assert "resident_runtimes" not in block


def _rendered_documents(*extra_args: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "test", str(CHART_DIR), *extra_args],
        check=True,
        capture_output=True,
        text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def test_credential_enrollment_runner_renders_unsupported_by_default() -> None:
    documents = _rendered_documents()
    configmap = next(
        doc
        for doc in documents
        if doc.get("kind") == "ConfigMap"
        and doc.get("metadata", {}).get("name") == "test-niuu-shared"
    )
    config = yaml.safe_load(configmap["data"]["config.yaml"])

    runner = config["credential_enrollment_runner"]
    assert runner["adapter"].endswith("UnsupportedCredentialEnrollmentRunner")
    assert runner["secret_kwargs_env"] == {}


def test_native_login_runner_has_dedicated_namespace_and_scoped_rbac():
    documents = _rendered_documents(
        "--set",
        "credentialEnrollmentRunner.adapter="
        "volundr.adapters.outbound.k8s_login_runner.KubernetesLoginRunner",
        "--set",
        "credentialEnrollmentRunner.kwargs.image=test-only/skuld:pinned",
        "--set",
        "credentialEnrollmentRunner.kwargs.namespace=test-logins",
    )
    namespace = next(doc for doc in documents if doc["kind"] == "Namespace")
    assert namespace["metadata"]["name"] == "test-logins"
    role = next(doc for doc in documents if doc["kind"] == "Role")
    assert role["metadata"]["namespace"] == "test-logins"
    assert {resource for rule in role["rules"] for resource in rule["resources"]} == {
        "jobs",
        "pods",
        "pods/exec",
    }
    binding = next(doc for doc in documents if doc["kind"] == "RoleBinding")
    assert binding["subjects"][0]["namespace"] == "default"


def test_credential_enrollment_runner_secret_kwargs_reach_the_deployment() -> None:
    documents = _rendered_documents(
        "--set",
        (
            "credentialEnrollmentRunner.adapter=volundr.adapters.outbound."
            "credential_enrollment_runner.OpenShellCredentialEnrollmentRunner"
        ),
        "--set",
        "credentialEnrollmentRunner.secretKwargs[0].kwarg=client_secret",
        "--set",
        "credentialEnrollmentRunner.secretKwargs[0].secretName=openshell-volundr-agent-oidc",
        "--set",
        "credentialEnrollmentRunner.secretKwargs[0].secretKey=client-secret",
    )
    configmap = next(
        doc
        for doc in documents
        if doc.get("kind") == "ConfigMap"
        and doc.get("metadata", {}).get("name") == "test-niuu-shared"
    )
    config = yaml.safe_load(configmap["data"]["config.yaml"])
    assert config["credential_enrollment_runner"]["secret_kwargs_env"] == {
        "client_secret": "CREDENTIAL_ENROLLMENT_RUNNER_SK_CLIENT_SECRET"
    }

    deployment = next(doc for doc in documents if doc.get("kind") == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item for item in container["env"]}
    secret_ref = env["CREDENTIAL_ENROLLMENT_RUNNER_SK_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"]
    assert secret_ref == {"name": "openshell-volundr-agent-oidc", "key": "client-secret"}


def _provenance_config_and_env(*extra_args: str) -> tuple[dict, dict]:
    documents = _rendered_documents(*extra_args)
    configmap = next(
        doc
        for doc in documents
        if doc.get("kind") == "ConfigMap"
        and doc.get("metadata", {}).get("name") == "test-niuu-shared"
    )
    config = yaml.safe_load(configmap["data"]["config.yaml"])
    deployment = next(doc for doc in documents if doc.get("kind") == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return config["git"]["provenance"], {item["name"]: item for item in container["env"]}


def test_image_provenance_is_enabled_by_default_with_a_writable_trust_root_cache() -> None:
    provenance, env = _provenance_config_and_env()

    # No kwargs rendered: the code's defaults (issuer, signing workflows) apply.
    assert provenance == {"enabled": True, "bundle_fetch_timeout_seconds": 30}
    # sigstore-python caches its TUF trust root under XDG dirs; the runtime user has no home.
    assert env["XDG_DATA_HOME"]["value"] == "/tmp/xdg-data"
    assert env["XDG_CACHE_HOME"]["value"] == "/tmp/xdg-cache"


def test_image_provenance_can_be_turned_off_and_then_needs_no_cache_dirs() -> None:
    provenance, env = _provenance_config_and_env("--set", "git.provenance.enabled=false")

    assert provenance["enabled"] is False
    assert "XDG_DATA_HOME" not in env
    assert "XDG_CACHE_HOME" not in env


def test_image_provenance_verifier_kwargs_and_timeout_reach_the_config() -> None:
    provenance, _ = _provenance_config_and_env(
        "--set-json",
        'git.provenance.kwargs={"issuer":"https://issuer.test","workflow_paths":["a.yaml"]}',
        "--set",
        "git.provenance.bundleFetchTimeoutSeconds=75",
    )

    assert provenance["kwargs"] == {"issuer": "https://issuer.test", "workflow_paths": ["a.yaml"]}
    assert provenance["bundle_fetch_timeout_seconds"] == 75
