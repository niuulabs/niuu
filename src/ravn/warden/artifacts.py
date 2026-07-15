"""Pure helpers for rendering warden runtime artifacts."""

from __future__ import annotations

import os
import plistlib
import shlex
import sys
from pathlib import Path

import yaml

from niuu.domain.model_runtime import (
    session_definition_for_model,
    transport_adapter_for_session_definition,
)
from ravn.warden.models import WardenSpec


def confined_path(root: Path, *parts: str) -> Path:
    """Return a resolved child path that cannot escape *root*."""
    resolved_root = root.expanduser().resolve()
    candidate = resolved_root.joinpath(*parts).resolve()
    if candidate == resolved_root or not candidate.is_relative_to(resolved_root):
        raise ValueError("warden path must remain within its configured root")
    return candidate


def service_label(warden_id: str) -> str:
    """Return the canonical supervisor label for one warden."""
    return f"dev.niuu.ravn.warden.{warden_id}"


def runtime_config_path(warden_dir: Path) -> Path:
    """Return the generated runtime config path for one warden."""
    return confined_path(warden_dir, "config.yaml")


def log_path(warden_dir: Path) -> Path:
    """Return the stdout log path for one warden."""
    return confined_path(warden_dir, "warden.log")


def error_log_path(warden_dir: Path) -> Path:
    """Return the stderr log path for one warden."""
    return confined_path(warden_dir, "warden.error.log")


def local_python_executable() -> str:
    """Return the interpreter path used for local supervisor launches."""
    executable = (sys.executable or "").strip()
    if executable:
        return executable
    return "/usr/bin/python3"


def supervisor_environment(spec: WardenSpec) -> dict[str, str]:
    """Return the local supervisor environment for one warden."""
    env: dict[str, str] = {}

    for env_name in {"HOME", "PATH", "PYTHONPATH", "VIRTUAL_ENV"}:
        value = str(os.environ.get(env_name, "")).strip()
        if value:
            env[env_name] = value

    runtime_environment = _runtime_transport_environment(spec.model)
    if not runtime_environment:
        for env_name in _credential_env_names(spec.model):
            value = str(os.environ.get(env_name, "")).strip()
            if value:
                env[env_name] = value

    env["RAVN_LLM__MODEL"] = spec.model
    env.update(runtime_environment)
    env.update(_broker_environment(spec.broker))
    return env


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
    warden_dir: Path | None = None,
) -> dict:
    """Build the generated ravn daemon config for one persisted warden."""
    read_mounts = spec.mimir.read_mount_names or spec.mimir.mount_names
    write_mounts = spec.mimir.write_mount_names or (
        [spec.mimir.write_mount] if spec.mimir.write_mount else []
    )
    if not read_mounts and write_mounts:
        read_mounts = list(write_mounts)
    if not write_mounts and read_mounts:
        write_mounts = [read_mounts[0]]
    all_mounts = list(dict.fromkeys([*read_mounts, *write_mounts]))
    state_root = (
        warden_dir.expanduser().resolve()
        if warden_dir is not None
        else confined_path(Path.home() / ".ravn" / "wardens", spec.id)
    )
    queue_journal_path = state_root / "queue.json"
    daemon_state_dir = state_root / "state"

    instances = []
    for index, mount_name in enumerate(all_mounts):
        instances.append(
            {
                "name": mount_name,
                "role": "local" if index == 0 else "shared",
                "path": f"~/.ravn/mimir/{mount_name}",
                "read_priority": max(1, 20 - index),
            }
        )

    mimir_mcp_servers = _mimir_mcp_servers(all_mounts)
    payload = {
        "permission": {
            "mode": "workspace_write",
            "workspace_root": str((workspace_root or Path.cwd()).resolve()),
        },
        "logging": {
            "level": "info",
            "format": "text",
        },
        "llm": {
            "model": spec.model,
        },
        "mcp_servers": mimir_mcp_servers,
        "initiative": {
            "enabled": True,
            "default_persona": spec.persona,
            "queue_journal_path": str(queue_journal_path),
        },
        "gateway": {
            "channels": {
                "http": {
                    "enabled": spec.console.enabled,
                    "host": spec.console.host,
                    "port": spec.console.port,
                }
            }
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
            "persona": spec.persona,
            "cron_expression": spec.schedules.dream_cycle_cron_expression,
            "poll_interval_seconds": spec.schedules.dream_cycle_poll_interval_seconds,
            "state_dir": str(daemon_state_dir),
        },
        "mimir": {
            "enabled": True,
            "instances": instances,
            "write_routing": {
                "default": write_mounts or ["local"],
            },
            "source_trigger": {
                "enabled": spec.features.source_trigger_enabled,
                "persona": spec.persona,
                "poll_interval_seconds": spec.schedules.source_trigger_poll_interval_seconds,
            },
            "staleness_trigger": {
                "enabled": spec.features.staleness_trigger_enabled,
                "persona": spec.persona,
                "schedule_hours": spec.schedules.staleness_trigger_schedule_hours,
            },
        },
    }
    if not _runtime_transport_environment(spec.model):
        payload["llm"]["provider"] = _llm_provider_config(spec.model)
    skuld_payload = _skuld_runtime_config(spec.broker)
    if skuld_payload:
        payload["skuld"] = skuld_payload
    return payload


def _broker_environment(broker: dict[str, object]) -> dict[str, str]:
    """Return Skuld env overrides configured directly on a warden."""
    env: dict[str, str] = {}
    cli_type = str(broker.get("cliType") or broker.get("cli_type") or "").strip()
    if cli_type:
        env["SKULD__CLI_TYPE"] = cli_type

    transport = str(broker.get("transport") or "").strip()
    if transport:
        env["SKULD__TRANSPORT"] = transport

    transport_adapter = str(
        broker.get("transportAdapter") or broker.get("transport_adapter") or ""
    ).strip()
    if transport_adapter:
        env["SKULD__TRANSPORT_ADAPTER"] = transport_adapter

    if "skipPermissions" in broker or "skip_permissions" in broker:
        value = broker.get("skipPermissions", broker.get("skip_permissions"))
        env["SKULD__SKIP_PERMISSIONS"] = str(bool(value)).lower()

    approval_policy = str(
        broker.get("approvalPolicy") or broker.get("approval_policy") or ""
    ).strip()
    if approval_policy:
        env["SKULD__APPROVAL_POLICY"] = approval_policy

    sandbox = str(broker.get("sandbox") or "").strip()
    if sandbox:
        env["SKULD__SANDBOX"] = sandbox

    return env


def _skuld_runtime_config(broker: dict[str, object]) -> dict[str, object]:
    """Return Skuld config fields configured directly on a warden."""
    payload: dict[str, object] = {}
    if "skipPermissions" in broker or "skip_permissions" in broker:
        payload["skip_permissions"] = bool(
            broker.get("skipPermissions", broker.get("skip_permissions"))
        )

    approval_policy = str(
        broker.get("approvalPolicy") or broker.get("approval_policy") or ""
    ).strip()
    if approval_policy:
        payload["approval_policy"] = approval_policy

    sandbox = str(broker.get("sandbox") or "").strip()
    if sandbox:
        payload["sandbox"] = sandbox

    return payload


def _llm_provider_config(model: str) -> dict:
    from niuu.domain.model_runtime import vendor_for_model

    vendor = vendor_for_model(model)
    if vendor == "openai":
        return {
            "adapter": "ravn.adapters.llm.openai.OpenAICompatibleAdapter",
            "secret_kwargs_env": {
                "api_key": "OPENAI_API_KEY",
            },
        }
    if vendor == "anthropic":
        return {
            "adapter": "ravn.adapters.llm.anthropic.AnthropicAdapter",
            "secret_kwargs_env": {
                "api_key": "ANTHROPIC_API_KEY",
            },
        }
    return {
        "adapter": "ravn.adapters.llm.anthropic.AnthropicAdapter",
        "secret_kwargs_env": {
            "api_key": "ANTHROPIC_API_KEY",
        },
    }


def _mimir_mcp_servers(mount_names: list[str]) -> list[dict[str, object]]:
    python_executable = local_python_executable()
    servers: list[dict[str, object]] = []
    for mount_name in mount_names:
        normalized = str(mount_name or "").strip()
        if not normalized:
            continue
        servers.append(
            {
                "name": f"mimir-{normalized}",
                "transport": "stdio",
                "command": python_executable,
                "args": [
                    "-m",
                    "mimir",
                    "mcp",
                    "--path",
                    f"~/.ravn/mimir/{normalized}",
                    "--name",
                    normalized,
                ],
                "env": {},
                "enabled": True,
            }
        )
    return servers


def _credential_env_names(model: str) -> list[str]:
    provider_config = _llm_provider_config(model)
    secret_names = list(provider_config.get("secret_kwargs_env", {}).values())
    return [name for name in secret_names if isinstance(name, str) and name.strip()]


def _runtime_transport_environment(model: str) -> dict[str, str]:
    try:
        from volundr.config import Settings as VolundrSettings

        settings = VolundrSettings()
    except Exception:
        return {}

    definition_key, error = session_definition_for_model(
        model,
        session_definitions=settings.session_definitions,
    )
    if error or not definition_key:
        return {}

    definition = settings.session_definitions.get(definition_key)
    if definition is None:
        return {}

    defaults = getattr(definition, "defaults", {}) or {}
    if not isinstance(defaults, dict):
        return {}

    broker = defaults.get("broker", {})
    if not isinstance(broker, dict):
        broker = {}

    env: dict[str, str] = {}
    cli_type = str(broker.get("cliType") or "").strip()
    if cli_type:
        env["SKULD__CLI_TYPE"] = cli_type

    transport = str(broker.get("transport") or "").strip()
    if transport:
        env["SKULD__TRANSPORT"] = transport

    transport_adapter = transport_adapter_for_session_definition(
        definition_key,
        session_definitions=settings.session_definitions,
    )
    if transport_adapter:
        env["SKULD__TRANSPORT_ADAPTER"] = transport_adapter

    return env


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
            runtime_config_payload(spec, workspace_root=workspace_root, warden_dir=warden_dir),
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
    environment = supervisor_environment(spec)
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
    if environment:
        payload["EnvironmentVariables"] = environment
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
    environment = supervisor_environment(spec)
    return "\n".join(
        [
            "[Unit]",
            f"Description=Ravn warden {spec.name}",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={working_directory}",
            f"ExecStart={exec_start}",
            *[f"Environment={key}={value}" for key, value in sorted(environment.items())],
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
