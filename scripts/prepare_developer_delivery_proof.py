"""Prepare an isolated Docker proof stack for developer delivery.

This writes no credentials from existing installations and starts nothing. The
operator must provide a real digest-pinned verification image and a canonical
local clone below the proof root.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

IMAGE_DIGEST = re.compile(r"^.+@sha256:[a-f0-9]{64}$")
GIT_OBJECT = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
PRODUCERS = (
    "forge-service",
    "workstream-runner",
    "developer-code-reviewer",
    "developer-security-reviewer",
    "developer-adversarial-reviewer",
    "developer-integration-verifier",
)


def _parse_pinned_inputs(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        path, separator, digest = value.partition("=")
        candidate = Path(path)
        if (
            not separator
            or not path
            or candidate.is_absolute()
            or ".." in candidate.parts
            or not GIT_OBJECT.fullmatch(digest)
        ):
            raise ValueError("Pinned inputs must use relative/path=<git-blob-sha>")
        result[path] = digest
    if not result:
        raise ValueError("At least one pinned verification input is required")
    return result


def _ensure_signing_key(path: Path) -> None:
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise ValueError(f"Existing signing key is too permissive: {path}")
        return
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    encoded = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)


def _policy(
    *,
    require_forge_checks: bool,
    test_contracts: dict[str, list[str]],
    required_contracts: list[str],
) -> dict[str, object]:
    return {
        "required_review_roles": (
            ["integration"] if require_forge_checks else ["code", "security", "adversarial"]
        ),
        "required_test_contract_ids": required_contracts,
        "review_producers": {
            "code": ["developer-code-reviewer"],
            "security": ["developer-security-reviewer"],
            "adversarial": ["developer-adversarial-reviewer"],
            "integration": ["developer-integration-verifier"],
        },
        "test_producers": {name: ["workstream-runner"] for name in test_contracts},
        "required_check_names": ["proof-ci"] if require_forge_checks else [],
        "require_forge_checks": require_forge_checks,
        "forge_producers": ["forge-service"] if require_forge_checks else [],
        "integration_producers": ["workstream-runner"],
    }


def build_config(
    *,
    root: Path,
    repository_root: Path,
    repository_url: str | None = None,
    runner_image: str,
    pinned_inputs: dict[str, str],
    platform_image: str,
    skuld_image: str,
    port: int,
    contracts: dict[str, object] | None = None,
) -> dict[str, object]:
    root = root.resolve()
    repository_root = repository_root.resolve()
    if not repository_root.is_relative_to(root):
        raise ValueError("Canonical repository must be below the proof root")
    if not (repository_root / ".git").exists():
        raise ValueError("Canonical repository must be a non-bare Git checkout")
    if repository_url is not None and (
        not repository_url or repository_url != repository_url.strip()
    ):
        raise ValueError("Repository URL must be a non-empty exact identity")
    if not IMAGE_DIGEST.fullmatch(runner_image):
        raise ValueError("Verification runner image must be pinned with @sha256")
    definitions = (
        {
            "commands": {
                "proof-unit": ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
            },
            "workstream_required": ["proof-unit"],
            "integration_required": ["proof-unit"],
        }
        if contracts is None
        else contracts
    )
    if not isinstance(definitions, dict):
        raise ValueError("Verification contracts must be a mapping")
    test_contracts = definitions.get("commands")
    if (
        not isinstance(test_contracts, dict)
        or not test_contracts
        or any(
            not isinstance(name, str)
            or not isinstance(argv, list)
            or not argv
            or any(not isinstance(arg, str) or not arg for arg in argv)
            for name, argv in test_contracts.items()
        )
    ):
        raise ValueError("Contract commands must map names to non-empty argument vectors")
    for field in ("workstream_required", "integration_required"):
        required = definitions.get(field)
        if (
            not isinstance(required, list)
            or not required
            or any(not isinstance(name, str) or name not in test_contracts for name in required)
        ):
            raise ValueError(f"{field} must name configured contract commands")
    signing_key = root / "signing" / "evidence-private.pem"
    producer_keys = {producer: ["developer-proof-key"] for producer in PRODUCERS}
    authenticator = {
        "adapter": "niuu.adapters.evidence_signing.RsaEvidenceAuthenticator",
        "kwargs": {
            "key_id": "developer-proof-key",
            "private_key_pem_file": str(signing_key),
            "producer_keys": producer_keys,
        },
    }
    return {
        "mode": "docker",
        "server": {"host": "127.0.0.1", "port": port},
        "docker": {
            "project_name": "niuu-developer-delivery-proof",
            "data_dir": str(root),
            "compose_dir": str(root / "compose"),
            "bind_host": "127.0.0.1",
            "image": platform_image,
            "skuld_image": skuld_image,
            "require_gpu": False,
            "vllm": {"enabled": False},
            "model_server": {"enabled": False},
        },
        "pod_manager": {
            "adapter": ("volundr.adapters.outbound.docker_container.DockerContainerPodManager"),
            "workspaces_dir": str(root / "workspaces"),
            "sandbox_sessions_dir": str(root / "workspaces"),
            "state_file": str(root / "forge-state.json"),
            "allowed_mount_prefixes": str(root / "delivery" / "workspaces"),
            "max_concurrent": 8,
        },
        "local_mounts": {
            "enabled": True,
            "mini_mode": True,
            "allowed_prefixes": [str(root / "delivery" / "workspaces")],
            "default_read_only": False,
        },
        "delivery": {
            "enabled": True,
            "authenticator": authenticator,
            "workstreams": {
                "adapter": (
                    "volundr.adapters.outbound.workstream_git.LocalGitWorkstreamRepository"
                ),
                "kwargs": {
                    "workspace_root": str(root / "delivery" / "workspaces"),
                    "evidence_root": str(root / "delivery" / "evidence"),
                    "repository_roots": [str(repository_root.parent)],
                    "repository_paths": (
                        {repository_url: str(repository_root)} if repository_url else {}
                    ),
                    "isolation_mode": "clone",
                    "test_contracts": {
                        name: {
                            "image": runner_image,
                            "argv": argv,
                            "pinned_inputs": pinned_inputs,
                        }
                        for name, argv in test_contracts.items()
                    },
                },
            },
            "authorizer": {
                "adapter": (
                    "volundr.adapters.outbound.delivery_authorization.HttpDeliveryAuthorizer"
                ),
                "kwargs": {
                    "base_url": f"http://127.0.0.1:{port}",
                    "allow_anonymous_dev": True,
                },
            },
            "producer_id": "forge-service",
            "workstream_producer_id": "workstream-runner",
            "trusted_producers": list(PRODUCERS),
            "policies": {
                "developer-workstream": _policy(
                    require_forge_checks=False,
                    test_contracts=test_contracts,
                    required_contracts=definitions["workstream_required"],
                ),
                "developer-integration": _policy(
                    require_forge_checks=True,
                    test_contracts=test_contracts,
                    required_contracts=definitions["integration_required"],
                ),
            },
        },
        "developer_execution": {
            "enabled": True,
            "gateway_kwargs": {
                "base_url": f"http://niuu:{port}",
                "anonymous_dev_mode": True,
            },
            "review_authenticator_adapter": authenticator["adapter"],
            "review_authenticator_kwargs": authenticator["kwargs"],
            "review_producers": {
                "code": "developer-code-reviewer",
                "security": "developer-security-reviewer",
                "adversarial": "developer-adversarial-reviewer",
            },
            "evidence_policy_id": "developer-workstream",
            "integration_policy_id": "developer-integration",
        },
        "volundr": {
            "url": f"http://127.0.0.1:{port}",
            "use_connection_factory_in_dev": False,
        },
        "a2a": {"public_base_url": f"http://niuu:{port}"},
        "auth": {"default_tenant_id": "default"},
        "dispatch": {
            "flock": {
                "ravn_config": {
                    "gateway": {
                        "platform": {
                            "anonymous_dev_mode": True,
                            "base_url": f"http://niuu:{port}",
                        }
                    }
                }
            }
        },
    }


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=repository / ".niuu" / "data" / "developer-proof",
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument(
        "--repository-url",
        help="Exact approved repository URL mapped to the canonical local clone",
    )
    parser.add_argument("--runner-image", required=True)
    parser.add_argument("--pinned-input", action="append", default=[])
    parser.add_argument("--contracts-file", type=Path)
    parser.add_argument("--platform-image", default="niuu-developer-proof:local")
    parser.add_argument("--skuld-image", default="skuld-developer-proof:local")
    parser.add_argument("--port", type=int, default=8180)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        pinned_inputs = _parse_pinned_inputs(args.pinned_input)
        config = build_config(
            root=args.root,
            repository_root=args.repository_root,
            repository_url=args.repository_url,
            runner_image=args.runner_image,
            pinned_inputs=pinned_inputs,
            platform_image=args.platform_image,
            skuld_image=args.skuld_image,
            port=args.port,
            contracts=(
                yaml.safe_load(args.contracts_file.read_text()) if args.contracts_file else None
            ),
        )
        args.root.mkdir(parents=True, exist_ok=True)
        _ensure_signing_key(args.root.resolve() / "signing" / "evidence-private.pem")
        config_path = args.root.resolve() / "config.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        config_path.chmod(0o600)
    except ValueError as exc:
        parser.error(str(exc))
    print(config_path)


if __name__ == "__main__":
    main()
