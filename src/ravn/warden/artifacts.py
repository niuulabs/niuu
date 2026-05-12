"""Pure helpers for rendering warden runtime artifacts."""

from __future__ import annotations

import plistlib
import shlex
import sys
from pathlib import Path

import yaml

from ravn.warden.models import WardenSpec


def service_label(warden_id: str) -> str:
    """Return the canonical supervisor label for one warden."""
    return f"dev.niuu.ravn.warden.{warden_id}"


def runtime_config_path(warden_dir: Path) -> Path:
    """Return the generated runtime config path for one warden."""
    return warden_dir / "config.yaml"


def log_path(warden_dir: Path) -> Path:
    """Return the stdout log path for one warden."""
    return warden_dir / "warden.log"


def error_log_path(warden_dir: Path) -> Path:
    """Return the stderr log path for one warden."""
    return warden_dir / "warden.error.log"


def local_python_executable() -> str:
    """Return the interpreter path used for local supervisor launches."""
    executable = (sys.executable or "").strip()
    if executable:
        return executable
    return "/usr/bin/python3"


def start_command(spec: WardenSpec, *, config_path: Path) -> str:
    """Return the shell-safe command used to launch the daemon."""
    args = ["ravn", "daemon", "--config", str(config_path), "--persona", spec.persona]
    if spec.profile:
        args.extend(["--profile", spec.profile])
    return shlex.join(args)


def daemon_program_arguments(spec: WardenSpec, *, config_path: str) -> list[str]:
    """Return the argv used to launch a daemonized warden."""
    args = [
        "/usr/bin/env",
        "ravn",
        "daemon",
        "--config",
        config_path,
        "--persona",
        spec.persona,
    ]
    if spec.profile:
        args.extend(["--profile", spec.profile])
    return args


def local_start_command(spec: WardenSpec, *, config_path: Path, python_executable: str) -> str:
    """Return the explicit local supervisor command used to launch the daemon."""
    args = local_daemon_program_arguments(
        spec,
        config_path=str(config_path),
        python_executable=python_executable,
    )
    return shlex.join(args)


def local_daemon_program_arguments(
    spec: WardenSpec,
    *,
    config_path: str,
    python_executable: str,
) -> list[str]:
    """Return explicit argv for launchd/systemd style local supervisor launches."""
    args = [
        python_executable,
        "-m",
        "ravn",
        "daemon",
        "--config",
        config_path,
        "--persona",
        spec.persona,
    ]
    if spec.profile:
        args.extend(["--profile", spec.profile])
    return args


def runtime_config_payload(
    spec: WardenSpec,
    *,
    workspace_root: Path | None = None,
) -> dict:
    """Build the generated ravn daemon config for one persisted warden."""
    write_mount = spec.mimir.write_mount or (
        spec.mimir.mount_names[0] if spec.mimir.mount_names else "local"
    )
    instances = []
    for index, mount_name in enumerate(spec.mimir.mount_names):
        instances.append(
            {
                "name": mount_name,
                "role": "local" if index == 0 else "shared",
                "path": f"~/.ravn/mimir/{mount_name}",
                "read_priority": max(1, 20 - index),
            }
        )

    return {
        "permission": {
            "mode": "workspace_write",
            "workspace_root": str((workspace_root or Path.cwd()).resolve()),
        },
        "initiative": {
            "enabled": True,
            "default_persona": spec.persona,
        },
        "thread": {
            "enabled": spec.features.thread_queue_enabled,
        },
        "wakefulness": {
            "enabled": spec.features.wakefulness_enabled,
        },
        "recap": {
            "enabled": spec.features.recap_enabled,
        },
        "dream_cycle": {
            "enabled": spec.features.dream_cycle_enabled,
            "persona": "mimir-curator",
        },
        "mimir": {
            "enabled": True,
            "instances": instances,
            "write_routing": {
                "default": [write_mount],
            },
            "source_trigger": {
                "enabled": spec.features.source_trigger_enabled,
                "persona": spec.persona,
            },
            "staleness_trigger": {
                "enabled": spec.features.staleness_trigger_enabled,
                "persona": "mimir-curator",
            },
        },
    }


def write_runtime_config(
    spec: WardenSpec,
    *,
    warden_dir: Path,
    workspace_root: Path | None = None,
) -> Path:
    """Render and persist the runtime config for one warden."""
    config_path = runtime_config_path(warden_dir)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(
            runtime_config_payload(spec, workspace_root=workspace_root),
            sort_keys=False,
            allow_unicode=False,
        ),
        encoding="utf-8",
    )
    return config_path


def render_launchd_plist(
    spec: WardenSpec,
    *,
    config_path: Path,
    working_directory: Path,
    stdout_path: Path,
    stderr_path: Path,
    python_executable: str,
) -> str:
    """Render the launchd plist for one warden."""
    payload = {
        "Label": service_label(spec.id),
        "ProgramArguments": local_daemon_program_arguments(
            spec,
            config_path=str(config_path),
            python_executable=python_executable,
        ),
        "RunAtLoad": spec.autostart,
        "KeepAlive": True,
        "WorkingDirectory": str(working_directory),
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
    }
    return plistlib.dumps(payload).decode("utf-8")


def render_systemd_unit(
    spec: WardenSpec,
    *,
    config_path: Path,
    working_directory: Path,
    python_executable: str,
) -> str:
    """Render the systemd user unit for one warden."""
    exec_start = local_start_command(
        spec,
        config_path=config_path,
        python_executable=python_executable,
    )
    return "\n".join(
        [
            "[Unit]",
            f"Description=Ravn warden {spec.name}",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={working_directory}",
            f"ExecStart={exec_start}",
            "Restart=always",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def manifest_bundle_path(warden_dir: Path) -> Path:
    """Return the local manifest bundle path for Kubernetes-backed wardens."""
    return warden_dir / "k8s-bundle.yaml"


def render_k8s_bundle(
    spec: WardenSpec,
    *,
    namespace: str,
    image: str,
    replicas: int,
    config_text: str,
    config_mount_path: str = "/etc/ravn/config.yaml",
    create_namespace: bool = False,
    image_pull_policy: str = "IfNotPresent",
    service_account_name: str = "",
    extra_env: dict[str, str] | None = None,
) -> str:
    """Render a multi-document Kubernetes manifest bundle for one warden."""
    name = service_label(spec.id).replace(".", "-")
    labels = {
        "app.kubernetes.io/name": "ravn-warden",
        "app.kubernetes.io/instance": spec.id,
        "app.kubernetes.io/managed-by": "ravn",
    }

    docs: list[dict] = []
    if create_namespace:
        docs.append(
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": namespace,
                },
            }
        )

    docs.extend(
        [
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": name,
                    "namespace": namespace,
                    "labels": labels,
                },
                "data": {
                    "config.yaml": config_text,
                },
            },
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {
                    "name": name,
                    "namespace": namespace,
                    "labels": labels,
                },
                "spec": {
                    "replicas": replicas,
                    "selector": {
                        "matchLabels": {
                            "app.kubernetes.io/instance": spec.id,
                        }
                    },
                    "template": {
                        "metadata": {
                            "labels": labels,
                        },
                        "spec": {
                            **(
                                {"serviceAccountName": service_account_name}
                                if service_account_name
                                else {}
                            ),
                            "containers": [
                                {
                                    "name": "ravn",
                                    "image": image,
                                    "imagePullPolicy": image_pull_policy,
                                    "command": daemon_program_arguments(
                                        spec,
                                        config_path=config_mount_path,
                                    ),
                                    "env": [
                                        {"name": key, "value": value}
                                        for key, value in sorted((extra_env or {}).items())
                                    ],
                                    "volumeMounts": [
                                        {
                                            "name": "ravn-config",
                                            "mountPath": config_mount_path,
                                            "subPath": "config.yaml",
                                            "readOnly": True,
                                        }
                                    ],
                                }
                            ],
                            "volumes": [
                                {
                                    "name": "ravn-config",
                                    "configMap": {
                                        "name": name,
                                    },
                                }
                            ],
                        },
                    },
                },
            },
        ]
    )
    return yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=False)


def write_k8s_bundle(
    spec: WardenSpec,
    *,
    warden_dir: Path,
    namespace: str,
    image: str,
    replicas: int,
    config_text: str,
    config_mount_path: str = "/etc/ravn/config.yaml",
    create_namespace: bool = False,
    image_pull_policy: str = "IfNotPresent",
    service_account_name: str = "",
    extra_env: dict[str, str] | None = None,
) -> Path:
    """Persist the Kubernetes manifest bundle for one warden."""
    bundle_path = manifest_bundle_path(warden_dir)
    bundle_path.write_text(
        render_k8s_bundle(
            spec,
            namespace=namespace,
            image=image,
            replicas=replicas,
            config_text=config_text,
            config_mount_path=config_mount_path,
            create_namespace=create_namespace,
            image_pull_policy=image_pull_policy,
            service_account_name=service_account_name,
            extra_env=extra_env,
        ),
        encoding="utf-8",
    )
    return bundle_path
