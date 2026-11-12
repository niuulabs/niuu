"""Generic session MCP contributor for attached session resources."""

from __future__ import annotations

import logging
import re
from typing import Any

from volundr.domain.models import LaunchSpec, Session
from volundr.domain.ports import (
    LaunchSpecProvider,
    SessionContext,
    SessionContribution,
    SessionContributor,
)
from volundr.domain.project_memory import MIMIR_SERVER_PREFIX

logger = logging.getLogger(__name__)

_DEFAULT_PYTHON_BINARY = "python3"
#: Where a session's ephemeral local Mimir mounts live; each mount is a
#: directory below this path.
MIMIR_MOUNT_PATH = "/mimir/local"
#: Where the workload-identity token is projected into the session.
_DEFAULT_WORKLOAD_IDENTITY_MOUNT_PATH = "/var/run/secrets/niuu-workload"
#: Where secret-backed Mimir credentials are mounted.
_SECRET_VOLUME_PATH = "/run/secrets"


class MimirMountConfigError(ValueError):
    """A session's Mimir mount configuration cannot be launched; the message says how to fix it."""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _secret_file_name(value: str) -> str:
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value)
    normalized = normalized.strip(".-_")
    return normalized or "credential"


def _mimir_token_file(auth_ref: str) -> str:
    return f"{_SECRET_VOLUME_PATH}/mimir/{_secret_file_name(auth_ref)}/token"


def _mimir_auth_from_ref(auth_ref: object) -> dict[str, Any] | None:
    normalized = str(auth_ref or "").strip()
    if not normalized:
        return None
    return {
        "type": "workload",
        "token_file": f"{_DEFAULT_WORKLOAD_IDENTITY_MOUNT_PATH}/token",
        "audiences": ["mimir"],
    }


def _normalize_mimir_workload_config(
    raw: dict[str, Any] | None,
    legacy_hosted_url: str | None,
) -> dict[str, Any]:
    normalized = dict(raw or {})
    if legacy_hosted_url and not normalized.get("hosted_url"):
        normalized["hosted_url"] = legacy_hosted_url
    return normalized


def _resolve_mimir_runtime(
    mimir_cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    instances: list[dict[str, Any]] = []
    known_mounts: set[str] = set()
    write_rules: list[dict[str, Any]] = []
    default_mounts: list[str] = []
    hosted_url = str(mimir_cfg.get("hosted_url") or "").strip()
    registry_refs = list(mimir_cfg.get("registry_refs") or [])
    ephemeral_locals = list(mimir_cfg.get("ephemeral_locals") or [])

    for index, raw_instance in enumerate(list(mimir_cfg.get("instances") or [])):
        if not isinstance(raw_instance, dict):
            raise MimirMountConfigError(
                f"mimir.instances[{index}] must be an object with a name and a path, url or "
                f"adapter, not {type(raw_instance).__name__}; fix or remove it"
            )
        instance = _normalize_instance(raw_instance)
        if instance["name"] in known_mounts:
            raise MimirMountConfigError(
                f"mimir.instances names the mount {instance['name']!r} twice; give each "
                "instance its own name"
            )
        instances.append(instance)
        known_mounts.add(instance["name"])

    if hosted_url and not registry_refs and "hosted" not in known_mounts:
        instances.append(
            {
                "name": "hosted",
                "role": "shared",
                "url": hosted_url,
                "categories": ["entity", "decision", "directive", "topic"],
            }
        )
        known_mounts.add("hosted")
        write_rules.extend(
            [
                {"prefix": "project/", "mounts": ["hosted"]},
                {"prefix": "entity/", "mounts": ["hosted"]},
            ]
        )

    for index, raw_ref in enumerate(registry_refs):
        if not isinstance(raw_ref, dict):
            raise MimirMountConfigError(
                f"mimir.registry_refs[{index}] must be an object naming a mount, not "
                f"{type(raw_ref).__name__}; fix or remove it"
            )
        mount_name = str(
            raw_ref.get("mount_name")
            or raw_ref.get("mountName")
            or raw_ref.get("registry_entry_id")
            or raw_ref.get("registryEntryId")
            or raw_ref.get("resource_node_id")
            or raw_ref.get("resourceNodeId")
            or ""
        ).strip()
        if not mount_name:
            raise MimirMountConfigError(
                f"mimir.registry_refs[{index}] names no mount: set mount_name, "
                "registry_entry_id or resource_node_id"
            )
        if mount_name in known_mounts:
            raise MimirMountConfigError(
                f"The mount {mount_name!r} is configured twice in mimir (instances, "
                "registry_refs or ephemeral_locals); give each mount its own name"
            )

        instance: dict[str, Any] = {
            "name": mount_name,
            "role": str(raw_ref.get("role") or "shared"),
        }
        url = str(raw_ref.get("url") or "").strip()
        path = str(raw_ref.get("path") or "").strip()
        if raw_ref.get("adapter"):
            instance["adapter"] = str(raw_ref["adapter"])
            instance["kwargs"] = dict(raw_ref.get("kwargs") or {})
            instance["secret_kwargs_env"] = dict(raw_ref.get("secret_kwargs_env") or {})
            auth_ref = raw_ref.get("auth_ref") or raw_ref.get("authRef")
            if instance["adapter"] == "mimir.adapters.gbrain.GBrainMimirAdapter" and auth_ref:
                instance["kwargs"].pop("api_token", None)
                instance["secret_kwargs_env"].pop("api_token", None)
                instance["kwargs"]["api_token_file"] = _mimir_token_file(str(auth_ref))
        elif url:
            instance["url"] = url
        elif path:
            instance["path"] = path
        elif hosted_url:
            instance["url"] = hosted_url
        else:
            raise MimirMountConfigError(
                f"The mount {mount_name!r} has no adapter, url or path, so it cannot be "
                "reached. Resolve its registry entry before launch (the registry entry must "
                "exist and be enabled), or set mimir.hosted_url"
            )

        if auth := _mimir_auth_from_ref(raw_ref.get("auth_ref") or raw_ref.get("authRef")):
            instance["auth"] = auth

        if "default_read_priority" in raw_ref or "defaultReadPriority" in raw_ref:
            instance["read_priority"] = int(
                raw_ref.get("default_read_priority", raw_ref.get("defaultReadPriority"))
            )
        categories = _string_list(raw_ref.get("categories"))
        if categories:
            instance["categories"] = categories
        instances.append(instance)
        known_mounts.add(mount_name)

    for index, raw_local in enumerate(ephemeral_locals):
        if not isinstance(raw_local, dict):
            raise MimirMountConfigError(
                f"mimir.ephemeral_locals[{index}] must be an object naming a mount, not "
                f"{type(raw_local).__name__}; fix or remove it"
            )
        mount_name = str(
            raw_local.get("mount_name")
            or raw_local.get("mountName")
            or raw_local.get("resource_node_id")
            or raw_local.get("resourceNodeId")
            or ""
        ).strip()
        if not mount_name:
            raise MimirMountConfigError(
                f"mimir.ephemeral_locals[{index}] names no mount: set mount_name or "
                "resource_node_id"
            )
        if mount_name in known_mounts:
            raise MimirMountConfigError(
                f"The mount {mount_name!r} is configured twice in mimir (instances, "
                "registry_refs or ephemeral_locals); give each mount its own name"
            )

        instance = {
            "name": mount_name,
            "role": "local",
            "path": f"{MIMIR_MOUNT_PATH.rstrip('/')}/{mount_name}",
        }
        categories = _string_list(raw_local.get("categories"))
        if categories:
            instance["categories"] = categories
        instances.append(instance)
        known_mounts.add(mount_name)

    local_mount_names = [
        instance["name"]
        for instance in instances
        if str(instance.get("role") or "").strip() == "local"
    ]
    if local_mount_names:
        write_rules.append({"prefix": "self/", "mounts": [local_mount_names[0]]})

    for index, raw_binding in enumerate(list(mimir_cfg.get("bindings") or [])):
        if not isinstance(raw_binding, dict):
            raise MimirMountConfigError(
                f"mimir.bindings[{index}] must be an object naming a mount, not "
                f"{type(raw_binding).__name__}; fix or remove it"
            )
        mount_name = str(
            raw_binding.get("mount_name") or raw_binding.get("mountName") or ""
        ).strip()
        if mount_name not in known_mounts:
            raise MimirMountConfigError(
                f"mimir.bindings[{index}] is for the mount {mount_name!r}, which the session "
                f"does not mount (mounted: {', '.join(sorted(known_mounts)) or 'none'}); "
                "add the mount or remove the binding"
            )
        access = str(raw_binding.get("access") or "read")
        prefixes = _string_list(
            raw_binding.get("write_prefixes") or raw_binding.get("writePrefixes")
        )
        if raw_binding.get("enforce") is True:
            _hold_access_limit(
                next(i for i in instances if i["name"] == mount_name), access, prefixes
            )
        if "write" not in access:
            continue
        for prefix in prefixes:
            write_rules.append({"prefix": prefix, "mounts": [mount_name]})

    explicit_routing = mimir_cfg.get("write_routing") or {}
    if not isinstance(explicit_routing, dict):
        raise MimirMountConfigError(
            "mimir.write_routing must be an object with rules and default, not "
            f"{type(explicit_routing).__name__}"
        )
    for index, raw_rule in enumerate(list(explicit_routing.get("rules") or [])):
        if not isinstance(raw_rule, dict):
            raise MimirMountConfigError(
                f"mimir.write_routing.rules[{index}] must be an object with a prefix and "
                f"mounts, not {type(raw_rule).__name__}"
            )
        prefix = str(raw_rule.get("prefix") or "").strip()
        mounts = _string_list(raw_rule.get("mounts"))
        if not prefix or not mounts:
            raise MimirMountConfigError(
                f"mimir.write_routing.rules[{index}] needs a prefix and at least one mount"
            )
        _require_known(mounts, known_mounts, f"mimir.write_routing.rules[{index}].mounts")
        write_rules.append({"prefix": prefix, "mounts": mounts})

    explicit_defaults = _string_list(explicit_routing.get("default"))
    _require_known(explicit_defaults, known_mounts, "mimir.write_routing.default")
    if explicit_defaults:
        default_mounts = explicit_defaults

    configured_defaults = _string_list(mimir_cfg.get("default_mounts"))
    _require_known(configured_defaults, known_mounts, "mimir.default_mounts")
    if configured_defaults:
        default_mounts = configured_defaults
    else:
        local_defaults = [
            instance["name"]
            for instance in instances
            if str(instance.get("role") or "").strip() == "local"
        ]
        if local_defaults:
            default_mounts = [local_defaults[0]]
        elif len(instances) == 1:
            default_mounts = [instances[0]["name"]]

    return instances, {"rules": write_rules, "default": default_mounts}


def _hold_access_limit(instance: dict[str, Any], access: str, prefixes: list[str]) -> None:
    """Record the write limit of an enforced binding on ``instance``.

    A limit is held by the Mimir server the session starts for a path mount (its
    ``--read-only`` / ``--write-prefix`` flags). A mount the session reaches over a url
    or an adapter is served by someone else's Mimir, where this launch cannot hold it, so
    that is refused rather than started with a limit that does not hold.
    """
    if "write" in access and not prefixes:
        return
    name = instance["name"]
    if "path" not in instance:
        raise MimirMountConfigError(
            f"The mount {name!r} limits writes (access {access!r}, write prefixes "
            f"{prefixes or 'none'}), but it is served by its own Mimir over a url or an "
            "adapter, where the limit cannot be held. Give it unrestricted write access "
            "(that Mimir decides what the session may write), or mount a path"
        )
    if "write" not in access:
        if instance.get("write_prefixes"):
            raise MimirMountConfigError(
                f"The mount {name!r} has bindings that are both read-only and write-limited; "
                "keep one"
            )
        instance["read_only"] = True
        return
    if instance.get("read_only"):
        raise MimirMountConfigError(
            f"The mount {name!r} has bindings that are both read-only and write-limited; keep one"
        )
    instance["write_prefixes"] = sorted({*instance.get("write_prefixes", []), *prefixes})


def _require_known(names: list[str], known_mounts: set[str], where: str) -> None:
    unknown = [name for name in names if name not in known_mounts]
    if unknown:
        raise MimirMountConfigError(
            f"{where} names {', '.join(map(repr, unknown))}, which the session does not "
            f"mount (mounted: {', '.join(sorted(known_mounts)) or 'none'}); add the mount or "
            "remove the name"
        )


def _normalize_instance(raw_instance: dict[str, Any]) -> dict[str, Any]:
    mount_name = str(raw_instance.get("name") or "").strip()
    if not mount_name:
        raise MimirMountConfigError("A mimir instance has no name; set name on every instance")

    instance: dict[str, Any] = {
        "name": mount_name,
        "role": str(raw_instance.get("role") or "shared"),
    }
    url = str(raw_instance.get("url") or "").strip()
    path = str(raw_instance.get("path") or "").strip()
    if raw_instance.get("adapter"):
        instance["adapter"] = str(raw_instance["adapter"])
        instance["kwargs"] = dict(raw_instance.get("kwargs") or {})
        instance["secret_kwargs_env"] = dict(raw_instance.get("secret_kwargs_env") or {})
    elif url:
        instance["url"] = url
    elif path:
        instance["path"] = path
    else:
        raise MimirMountConfigError(
            f"The mimir instance {mount_name!r} has no adapter, url or path; set one of them"
        )

    if "read_priority" in raw_instance:
        instance["read_priority"] = int(raw_instance["read_priority"])
    categories = _string_list(raw_instance.get("categories"))
    if categories:
        instance["categories"] = categories
    if isinstance(raw_instance.get("auth"), dict):
        instance["auth"] = dict(raw_instance["auth"])
    return instance


def _slugify_server_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-").lower()
    return slug or "resource"


def _describe_mimir_instance(instance: dict[str, Any]) -> str:
    role = str(instance.get("role") or "shared")
    categories = instance.get("categories") or []
    parts = [f"Mimir knowledge mount '{instance['name']}'", f"role: {role}"]
    if categories:
        parts.append("categories: " + ", ".join(str(category) for category in categories))
    return f"{parts[0]} ({'; '.join(parts[1:])})"


class SessionMCPContributor(SessionContributor):
    """Converts attached session resources into MCP server configs.

    The contributor is generic at the session layer: it inspects attached
    resources and emits ``mcpServers`` into session values. Mimir is simply the
    first supported resource type behind this contributor.
    """

    def __init__(
        self,
        *,
        launch_spec_provider: LaunchSpecProvider | None = None,
        python_binary: str = _DEFAULT_PYTHON_BINARY,
        **_extra: object,
    ) -> None:
        self._launch_spec_provider = launch_spec_provider
        self._python_binary = python_binary

    @property
    def name(self) -> str:
        return "session_mcp"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        workload_config = self._resolve_workload(context)
        if not workload_config:
            return SessionContribution()

        raw_mimir = workload_config.get("mimir")
        mimir_cfg = _normalize_mimir_workload_config(
            raw_mimir if isinstance(raw_mimir, dict) else None,
            workload_config.get("mimir_hosted_url"),
        )
        if not mimir_cfg:
            return SessionContribution()

        instances, _write_routing = _resolve_mimir_runtime(mimir_cfg)
        if not instances:
            return SessionContribution()

        mcp_servers = [self._build_mimir_server(instance) for instance in instances]
        return SessionContribution(values={"mcpServers": mcp_servers})

    def _resolve_workload(
        self,
        context: SessionContext,
    ) -> dict[str, Any]:
        if context.workload_config:
            return context.workload_config

        source = self._resolve_source(context)
        if source is None:
            return {}
        return dict(source.workload_config or {})

    def _resolve_source(self, context: SessionContext) -> LaunchSpec | None:
        if self._launch_spec_provider is None:
            return None

        if context.launch_spec:
            spec = self._launch_spec_provider.get(context.launch_spec)
            if spec is not None:
                return spec

        if context.workload_type != "session":
            return self._launch_spec_provider.get_default(context.workload_type)
        return None

    def _build_mimir_server(self, instance: dict[str, Any]) -> dict[str, Any]:
        mount_name = str(instance.get("name") or "").strip()

        server_name = f"{MIMIR_SERVER_PREFIX}{_slugify_server_name(mount_name)}"
        description = _describe_mimir_instance(instance)

        path = str(instance.get("path") or "").strip()
        if path:
            return {
                "name": server_name,
                "type": "stdio",
                "command": self._python_binary,
                "args": [
                    "-m",
                    "mimir",
                    "mcp",
                    "--path",
                    path,
                    "--name",
                    mount_name,
                    *(["--read-only"] if instance.get("read_only") else []),
                    *(
                        arg
                        for prefix in instance.get("write_prefixes") or []
                        for arg in ("--write-prefix", prefix)
                    ),
                ],
                "env": {},
                "description": description,
            }

        url = str(instance.get("url") or "").strip()
        if url:
            return {
                "name": server_name,
                "type": "http",
                "url": url,
                "description": description,
            }

        raise MimirMountConfigError(
            f"The mount {mount_name!r} is reached through an adapter with no url or path, so "
            "the session has no MCP endpoint for it. Give its registry entry the url of the "
            "Mimir that serves it, or remove the mount"
        )
