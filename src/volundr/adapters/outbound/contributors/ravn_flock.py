"""RavnFlockContributor — pod spec builder for raiding party (flock) pods.

When workload_type == "ravn_flock", this contributor replaces the default
single-CLI layout with:
  - Skuld container with mesh.enabled=true + nng ports + Sleipnir webhook
  - N ravn daemon sidecar containers (one per persona in workload_config.personas)
  - Per-sidecar initContainer that writes YAML config to an emptyDir volume
    mounted read-only at /etc/ravn/config.yaml (RAVN_CONFIG env var points here)
  - nng mesh ports allocated via the same scheme as ravn flock init
  - Mimir emptyDir volume for ephemeral local memory
  - Sleipnir webhook transport config in both skuld and ravn containers

Port allocation (mirrors ravn/cli/flock.py via niuu.mesh):
  pub       = base_port + index * 2
  rep       = base_port + index * 2 + 1
  handshake = base_port + 100 + index
  gateway   = base_port + 200 + index
"""

from __future__ import annotations

import logging
from typing import Any

import yaml

from niuu.domain.llm_merge import _SECURITY_KEYS, merge_llm
from niuu.mesh import nng_gateway_port_for as _gateway_port_for
from niuu.mesh import nng_ports_for as _ports_for
from volundr.domain.models import ForgeProfile, PodSpecAdditions, Session, WorkspaceTemplate
from volundr.domain.ports import (
    ProfileProvider,
    SessionContext,
    SessionContribution,
    SessionContributor,
    TemplateProvider,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_PORT = 7480
_DEFAULT_MAX_CONCURRENT_TASKS = 3
_MIMIR_VOLUME_NAME = "mimir-local"
_MIMIR_MOUNT_PATH = "/mimir/local"
_WORKSPACE_VOLUME_NAME = "workspace"
_WORKSPACE_MOUNT_PATH = "/workspace"
_RAVN_IMAGE_DEFAULT = "ghcr.io/niuulabs/ravn:latest"
_RAVN_CONFIG_MOUNT_PATH = "/etc/ravn/config.yaml"
_RAVN_CONFIG_VOLUME_PREFIX = "ravn-cfg"
_RAVN_CONFIG_DIR = "/etc/ravn"
_INIT_WRITER_IMAGE = "busybox:latest"

# Persona source modes
_PERSONA_SOURCE_FILESYSTEM = "filesystem"
_PERSONA_SOURCE_MOUNTED_VOLUME = "mountedVolume"
_PERSONA_SOURCE_HTTP = "http"

# Persona ConfigMap volume name (shared across all ravn sidecars in the pod)
_PERSONA_CM_VOLUME_NAME = "ravn-personas"
_PERSONA_CM_DEFAULT_NAME = "ravn-personas"
_PERSONA_CM_DEFAULT_MOUNT_PATH = "/etc/ravn/personas"
_PERSONA_TOKEN_ENV = "RAVN_VOLUNDR_TOKEN"


def _normalize_personas(raw: list) -> list[dict]:
    """Normalize personas to list[dict].

    Accepts both legacy ``list[str]`` and new ``list[dict]`` with per-persona
    overrides.  Mixed lists (some strings, some dicts) are also supported.

    Security keys (``allowed_tools``, ``forbidden_tools``) are stripped with
    a WARN log — they are not overridable at the workload_config layer.
    """
    result: list[dict] = []
    for entry in raw:
        if isinstance(entry, str):
            result.append({"name": entry})
            continue

        if not isinstance(entry, dict):
            logger.warning("ravn_flock: skipping non-str/dict persona entry: %r", entry)
            continue

        if "name" not in entry:
            logger.warning("ravn_flock: skipping persona dict without 'name': %r", entry)
            continue

        cleaned = dict(entry)
        for security_key in _SECURITY_KEYS:
            if security_key in cleaned:
                logger.warning(
                    "ravn_flock: dropping security key %r from persona %r — "
                    "not overridable at the workload_config layer",
                    security_key,
                    cleaned["name"],
                )
                del cleaned[security_key]
        result.append(cleaned)
    return result


def _split_workflow_edge_label(label: object) -> tuple[str, str]:
    if not isinstance(label, str) or "->" not in label:
        return "", ""
    source, target = label.split("->", 1)
    return source.strip(), target.strip()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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

    for raw_instance in list(mimir_cfg.get("instances") or []):
        if not isinstance(raw_instance, dict):
            continue
        instance = _normalize_instance(raw_instance)
        if instance is None:
            continue
        if instance["name"] in known_mounts:
            continue
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

    for raw_ref in registry_refs:
        if not isinstance(raw_ref, dict):
            continue
        mount_name = str(
            raw_ref.get("mount_name")
            or raw_ref.get("mountName")
            or raw_ref.get("registry_entry_id")
            or raw_ref.get("registryEntryId")
            or raw_ref.get("resource_node_id")
            or raw_ref.get("resourceNodeId")
            or ""
        ).strip()
        if not mount_name or mount_name in known_mounts:
            continue

        instance: dict[str, Any] = {
            "name": mount_name,
            "role": str(raw_ref.get("role") or "shared"),
        }
        path = str(raw_ref.get("path") or "").strip()
        url = str(raw_ref.get("url") or "").strip()
        if path:
            instance["path"] = path
        elif url:
            instance["url"] = url
        elif hosted_url:
            instance["url"] = hosted_url
        else:
            continue

        categories = _string_list(raw_ref.get("categories"))
        if categories:
            instance["categories"] = categories
        instances.append(instance)
        known_mounts.add(mount_name)

    for raw_local in ephemeral_locals:
        if not isinstance(raw_local, dict):
            continue
        mount_name = str(
            raw_local.get("mount_name")
            or raw_local.get("mountName")
            or raw_local.get("resource_node_id")
            or raw_local.get("resourceNodeId")
            or ""
        ).strip()
        if not mount_name or mount_name in known_mounts:
            continue

        instance = {
            "name": mount_name,
            "role": "local",
            "path": f"{_MIMIR_MOUNT_PATH.rstrip('/')}/{mount_name}",
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

    for raw_binding in list(mimir_cfg.get("bindings") or []):
        if not isinstance(raw_binding, dict):
            continue
        mount_name = str(
            raw_binding.get("mount_name") or raw_binding.get("mountName") or ""
        ).strip()
        if not mount_name or mount_name not in known_mounts:
            continue
        access = str(raw_binding.get("access") or "read")
        if "write" not in access:
            continue
        for prefix in _string_list(
            raw_binding.get("write_prefixes") or raw_binding.get("writePrefixes")
        ):
            write_rules.append({"prefix": prefix, "mounts": [mount_name]})

    explicit_routing = mimir_cfg.get("write_routing") or {}
    if isinstance(explicit_routing, dict):
        for raw_rule in list(explicit_routing.get("rules") or []):
            if not isinstance(raw_rule, dict):
                continue
            prefix = str(raw_rule.get("prefix") or "").strip()
            mounts = [
                mount for mount in _string_list(raw_rule.get("mounts")) if mount in known_mounts
            ]
            if not prefix or not mounts:
                continue
            write_rules.append({"prefix": prefix, "mounts": mounts})

        explicit_defaults = [
            mount
            for mount in _string_list(explicit_routing.get("default"))
            if mount in known_mounts
        ]
        if explicit_defaults:
            default_mounts = explicit_defaults

    configured_defaults = [
        mount for mount in _string_list(mimir_cfg.get("default_mounts")) if mount in known_mounts
    ]
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


def _requires_local_mimir_mount(instances: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(instance.get("path"), str)
        and (
            str(instance["path"]).strip() == _MIMIR_MOUNT_PATH
            or str(instance["path"]).strip().startswith(f"{_MIMIR_MOUNT_PATH.rstrip('/')}/")
        )
        for instance in instances
    )


def _normalize_instance(raw_instance: dict[str, Any]) -> dict[str, Any] | None:
    mount_name = str(raw_instance.get("name") or "").strip()
    if not mount_name:
        return None

    instance: dict[str, Any] = {
        "name": mount_name,
        "role": str(raw_instance.get("role") or "shared"),
    }
    path = str(raw_instance.get("path") or "").strip()
    url = str(raw_instance.get("url") or "").strip()
    if path:
        instance["path"] = path
    elif url:
        instance["url"] = url
    else:
        return None

    categories = _string_list(raw_instance.get("categories"))
    if categories:
        instance["categories"] = categories
    return instance


def _build_ravn_config(
    *,
    persona: str,
    persona_override: dict,
    global_llm: dict | None,
    index: int,
    peer_id: str,
    base_port: int,
    all_personas: list[str],
    skuld_peer_id: str,
    mimir_config: dict[str, Any],
    sleipnir_publish_urls: list[str],
    global_max_concurrent_tasks: int = _DEFAULT_MAX_CONCURRENT_TASKS,
    mesh_host: str = "0.0.0.0",
    persona_source_mode: str = _PERSONA_SOURCE_FILESYSTEM,
    persona_source_mount_path: str = _PERSONA_CM_DEFAULT_MOUNT_PATH,
    persona_source_http_base_url: str = "",
    workflow: dict[str, Any] | None = None,
) -> str:
    """Generate the ravn daemon YAML config for a single flock node.

    Per-persona overrides from *persona_override* are merged on top of
    *global_llm* via :func:`niuu.domain.llm_merge.merge_llm`.  The resulting
    effective LLM config, system_prompt_extra, and iteration_budget are all
    embedded in the sidecar YAML so that ravn can apply them at runtime.
    """
    pub, rep, _hs = _ports_for(index, base_port)
    gw = _gateway_port_for(index, base_port)

    peers: list[dict[str, str]] = [{"peer_id": skuld_peer_id}] + [
        {"peer_id": f"flock-{p}"} for p in all_personas if p != persona
    ]

    mimir_instances, mimir_write_routing = _resolve_mimir_runtime(mimir_config)

    max_tasks = persona_override.get("max_concurrent_tasks") or global_max_concurrent_tasks

    config: dict[str, Any] = {
        "persona": persona,
        "mesh": {
            "enabled": True,
            "adapter": "nng",
            "own_peer_id": peer_id,
            "nng": {
                "pub_sub_address": f"tcp://{mesh_host}:{pub}",
                "req_rep_address": f"tcp://{mesh_host}:{rep}",
            },
            "peers": peers,
        },
        "discovery": {"enabled": True, "adapter": "static"},
        "cascade": {"enabled": True},
        "gateway": {
            "enabled": True,
            "channels": {
                "http": {"enabled": True, "host": "0.0.0.0", "port": gw},
            },
        },
        "initiative": {
            "enabled": True,
            "max_concurrent_tasks": max_tasks,
        },
        "mimir": {
            "enabled": True,
            "instances": mimir_instances,
            "write_routing": mimir_write_routing,
        },
        "logging": {"level": "INFO"},
    }

    # Merge LLM config: global override → per-persona override (last wins).
    effective_llm = merge_llm(
        defaults=None,
        global_override=global_llm,
        persona_override=persona_override.get("llm"),
    )
    if effective_llm:
        config["llm"] = effective_llm

    # Per-persona behavioral overrides — applied by ravn at persona load time.
    # Both system_prompt_extra and iteration_budget must land in persona_overrides
    # so that PersonaOverridesConfig (ravn/config.py) picks them up via pydantic.
    # iteration_budget is also mirrored to initiative for future initiative-level use.
    po: dict = {}
    system_prompt_extra = persona_override.get("system_prompt_extra") or ""
    if system_prompt_extra.strip():
        po["system_prompt_extra"] = system_prompt_extra
    budget = persona_override.get("iteration_budget") or 0
    if budget:
        po["iteration_budget"] = int(budget)
        config["initiative"]["iteration_budget"] = int(budget)
    consumes_event_types = persona_override.get("consumes_event_types") or []
    if consumes_event_types:
        po["consumes_event_types"] = [str(event_type) for event_type in consumes_event_types]
    if po:
        config["persona_overrides"] = po

    if persona_source_mode == _PERSONA_SOURCE_MOUNTED_VOLUME:
        config["persona_source"] = {
            "adapter": "ravn.adapters.personas.mounted_volume.MountedVolumePersonaAdapter",
            "kwargs": {"mount_path": persona_source_mount_path},
        }
    elif persona_source_mode == _PERSONA_SOURCE_HTTP and persona_source_http_base_url:
        config["persona_source"] = {
            "adapter": "ravn.adapters.personas.http.HttpPersonaAdapter",
            "kwargs": {"base_url": persona_source_http_base_url},
        }

    if sleipnir_publish_urls:
        config["sleipnir"] = {
            "enabled": True,
            "transport": "webhook",
            "webhook": {"publish_urls": sleipnir_publish_urls},
        }

    if workflow:
        config["workflow"] = workflow

    return yaml.safe_dump(config, default_flow_style=False, sort_keys=False)


def _normalize_workflow_config(
    workflow: dict[str, Any] | None,
    initiative_context: str,
) -> dict[str, Any] | None:
    if not isinstance(workflow, dict):
        return None

    graph = workflow.get("graph")
    if not isinstance(graph, dict):
        return None

    return {
        "workflow_id": str(workflow.get("workflow_id") or ""),
        "name": str(workflow.get("name") or ""),
        "version": str(workflow.get("version") or ""),
        "scope": str(workflow.get("scope") or ""),
        "initial_context": initiative_context,
        "graph": graph,
    }


def _workflow_trigger_config(workflow: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(workflow, dict):
        return None

    graph = workflow.get("graph")
    if not isinstance(graph, dict):
        return None

    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    trigger = next(
        (
            node
            for node in nodes
            if isinstance(node, dict) and str(node.get("kind") or "") == "trigger"
        ),
        None,
    )
    if trigger is None:
        return None

    event_type = str(trigger.get("dispatchEvent") or "").strip()
    if not event_type:
        trigger_id = str(trigger.get("id") or "")
        for edge in edges:
            if not isinstance(edge, dict) or str(edge.get("source") or "") != trigger_id:
                continue
            _source_event, target_event = _split_workflow_edge_label(edge.get("label"))
            if target_event:
                event_type = target_event
                break

    if not event_type:
        return None

    return {
        "enabled": "true",
        "node_id": str(trigger.get("id") or ""),
        "label": str(trigger.get("label") or ""),
        "source": str(trigger.get("source") or "manual dispatch"),
        "event_type": event_type,
    }


def _default_flock_trigger_config(
    *,
    initiative_context: str,
    persona_dicts: list[dict[str, Any]],
) -> dict[str, str] | None:
    """Return the generic startup trigger for plain raid-executor flocks."""
    if not initiative_context.strip():
        return None

    persona_names = {
        str(persona.get("name") or "").strip()
        for persona in persona_dicts
        if isinstance(persona, dict)
    }
    if "raid-executor" not in persona_names:
        return None

    return {
        "enabled": "true",
        "node_id": "dispatch-root",
        "label": "Dispatch raid",
        "source": "tyr dispatch",
        "event_type": "raid.requested",
    }


class RavnFlockContributor(SessionContributor):
    """Contributes flock pod spec when workload_type == 'ravn_flock'.

    Resolves the profile or template from the session context, reads
    workload_config.personas + mesh/mimir/sleipnir settings, then:
      - Emits skuld mesh env vars (MESH_ENABLED, MESH_PEER_ID, nng addresses)
      - Emits one ravn sidecar container per persona with RAVN_CONFIG env
      - Emits per-sidecar initContainer + emptyDir volume for mounted config
      - Emits a Mimir emptyDir volume
      - Emits Sleipnir webhook env vars for both skuld and ravn containers

    No-ops silently when workload_type != 'ravn_flock'.
    """

    def __init__(
        self,
        *,
        template_provider: TemplateProvider | None = None,
        profile_provider: ProfileProvider | None = None,
        ravn_image: str = _RAVN_IMAGE_DEFAULT,
        base_port: int = _DEFAULT_BASE_PORT,
        mesh_host: str = "0.0.0.0",
        persona_source_mode: str = _PERSONA_SOURCE_FILESYSTEM,
        persona_source_configmap_name: str = _PERSONA_CM_DEFAULT_NAME,
        persona_source_mount_path: str = _PERSONA_CM_DEFAULT_MOUNT_PATH,
        persona_source_token_secret_name: str = "",
        persona_source_http_base_url: str = "",
        **_extra: object,
    ) -> None:
        self._template_provider = template_provider
        self._profile_provider = profile_provider
        self._ravn_image = ravn_image
        self._base_port = base_port
        self._mesh_host = mesh_host
        self._persona_source_mode = persona_source_mode
        self._persona_source_configmap_name = persona_source_configmap_name
        self._persona_source_mount_path = persona_source_mount_path
        self._persona_source_token_secret_name = persona_source_token_secret_name
        self._persona_source_http_base_url = persona_source_http_base_url

    @property
    def name(self) -> str:
        return "ravn_flock"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        # Check context first (workload_type from SpawnRequest / REST body)
        if context.workload_type == "ravn_flock" and context.workload_config:
            wc = context.workload_config
        else:
            # Fall back to template/profile resolution
            source = self._resolve_source(context)
            if source is None or source.workload_type != "ravn_flock":
                return SessionContribution()
            wc = source.workload_config
        raw_personas: list = list(wc.get("personas", []))

        if not raw_personas:
            logger.warning(
                "ravn_flock workload_config has no personas — skipping flock contribution"
            )
            return SessionContribution()

        # Normalize personas to list[dict] — accept both legacy list[str]
        # and new list[dict] with per-persona overrides.
        persona_dicts: list[dict] = _normalize_personas(raw_personas)

        mesh_cfg: dict = wc.get("mesh", {})
        mimir_cfg = _normalize_mimir_workload_config(
            wc.get("mimir") if isinstance(wc.get("mimir"), dict) else None,
            wc.get("mimir_hosted_url"),
        )
        sleipnir_cfg: dict = wc.get("sleipnir", {})

        sleipnir_publish_urls = list(
            sleipnir_cfg.get("publish_urls") or wc.get("sleipnir_publish_urls") or []
        )
        mesh_transport = str(wc.get("mesh_transport") or mesh_cfg.get("transport") or "nng")
        global_max_concurrent_tasks: int = wc.get(
            "max_concurrent_tasks", _DEFAULT_MAX_CONCURRENT_TASKS
        )
        global_llm: dict | None = wc.get("llm_config") or None
        initiative_context = str(wc.get("initiative_context") or "")
        workflow_cfg = _normalize_workflow_config(
            wc.get("workflow"),
            initiative_context,
        )

        values, pod_spec = self._build_flock_spec(
            session=session,
            persona_dicts=persona_dicts,
            mesh_transport=mesh_transport,
            mimir_config=mimir_cfg,
            sleipnir_publish_urls=sleipnir_publish_urls,
            global_max_concurrent_tasks=global_max_concurrent_tasks,
            global_llm=global_llm,
            initiative_context=initiative_context,
            persona_source_mode=self._persona_source_mode,
            persona_source_configmap_name=self._persona_source_configmap_name,
            persona_source_mount_path=self._persona_source_mount_path,
            persona_source_token_secret_name=self._persona_source_token_secret_name,
            persona_source_http_base_url=self._persona_source_http_base_url,
            workflow=workflow_cfg,
        )

        return SessionContribution(values=values, pod_spec=pod_spec)

    def _resolve_source(self, context: SessionContext) -> WorkspaceTemplate | ForgeProfile | None:
        if context.template_name and self._template_provider is not None:
            template = self._template_provider.get(context.template_name)
            if template is not None:
                return template

        if self._profile_provider is not None:
            if context.profile_name:
                profile = self._profile_provider.get(context.profile_name)
                if profile is not None:
                    return profile
            default = self._profile_provider.get_default("ravn_flock")
            if default is not None:
                return default

        return None

    def _build_flock_spec(
        self,
        session: Session,
        persona_dicts: list[dict],
        mesh_transport: str,
        mimir_config: dict[str, Any],
        sleipnir_publish_urls: list[str],
        global_max_concurrent_tasks: int,
        global_llm: dict | None = None,
        initiative_context: str = "",
        persona_source_mode: str = _PERSONA_SOURCE_FILESYSTEM,
        persona_source_configmap_name: str = _PERSONA_CM_DEFAULT_NAME,
        persona_source_mount_path: str = _PERSONA_CM_DEFAULT_MOUNT_PATH,
        persona_source_token_secret_name: str = "",
        persona_source_http_base_url: str = "",
        workflow: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], PodSpecAdditions]:
        session_id = str(session.id)
        base_port = self._base_port
        all_personas = [pd["name"] for pd in persona_dicts]
        mimir_instances, _mimir_write_routing = _resolve_mimir_runtime(mimir_config)
        requires_local_mimir_mount = _requires_local_mimir_mount(mimir_instances)

        # Skuld (index 0) + ravn nodes start at index 1
        skuld_peer_id = f"skuld-{session_id[:8]}"
        skuld_pub, skuld_rep, skuld_hs = _ports_for(0, base_port)

        skuld_env: list[dict] = [
            {"name": "MESH_ENABLED", "value": "true"},
            {"name": "MESH_TRANSPORT", "value": mesh_transport},
            {"name": "MESH_PEER_ID", "value": skuld_peer_id},
            {"name": "MESH_PUB_ADDRESS", "value": f"tcp://{self._mesh_host}:{skuld_pub}"},
            {"name": "MESH_REP_ADDRESS", "value": f"tcp://{self._mesh_host}:{skuld_rep}"},
            {"name": "MESH_HANDSHAKE_PORT", "value": str(skuld_hs)},
        ]
        workflow_trigger = _workflow_trigger_config(workflow)
        if workflow_trigger is None:
            workflow_trigger = _default_flock_trigger_config(
                initiative_context=initiative_context,
                persona_dicts=persona_dicts,
            )
        if workflow_trigger is not None:
            skuld_env.extend(
                [
                    {
                        "name": "SKULD__MESH__CONSUMES_EVENT_TYPES",
                        "value": "[]",
                    },
                    {
                        "name": "SKULD__WORKFLOW_TRIGGER__ENABLED",
                        "value": workflow_trigger["enabled"],
                    },
                    {
                        "name": "SKULD__WORKFLOW_TRIGGER__NODE_ID",
                        "value": workflow_trigger["node_id"],
                    },
                    {
                        "name": "SKULD__WORKFLOW_TRIGGER__LABEL",
                        "value": workflow_trigger["label"],
                    },
                    {
                        "name": "SKULD__WORKFLOW_TRIGGER__SOURCE",
                        "value": workflow_trigger["source"],
                    },
                    {
                        "name": "SKULD__WORKFLOW_TRIGGER__EVENT_TYPE",
                        "value": workflow_trigger["event_type"],
                    },
                ]
            )

        if sleipnir_publish_urls:
            skuld_env.append(
                {
                    "name": "SLEIPNIR_PUBLISH_URLS",
                    "value": ",".join(sleipnir_publish_urls),
                }
            )

        # nng ports for skuld as Helm values
        skuld_ports: list[dict] = [
            {"containerPort": skuld_pub, "name": "mesh-pub", "protocol": "TCP"},
            {"containerPort": skuld_rep, "name": "mesh-rep", "protocol": "TCP"},
            {"containerPort": skuld_hs, "name": "mesh-hs", "protocol": "TCP"},
        ]

        # Persona source volume (shared across all ravn sidecars)
        persona_source_volumes: list[dict] = []
        persona_source_volume_mounts: list[dict] = []
        persona_source_envs: list[dict] = []

        if persona_source_mode == _PERSONA_SOURCE_MOUNTED_VOLUME:
            persona_source_volumes.append(
                {
                    "name": _PERSONA_CM_VOLUME_NAME,
                    "configMap": {"name": persona_source_configmap_name},
                }
            )
            persona_source_volume_mounts.append(
                {
                    "name": _PERSONA_CM_VOLUME_NAME,
                    "mountPath": persona_source_mount_path,
                    "readOnly": True,
                }
            )
        elif persona_source_mode == _PERSONA_SOURCE_HTTP and persona_source_token_secret_name:
            persona_source_envs.append(
                {
                    "name": _PERSONA_TOKEN_ENV,
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": persona_source_token_secret_name,
                            "key": "token",
                        }
                    },
                }
            )

        # Ravn sidecar containers (indices 1..N)
        extra_containers: list[dict] = []
        config_volumes: list[dict] = []
        init_containers: list[dict] = []

        for i, persona_dict in enumerate(persona_dicts):
            persona = persona_dict["name"]
            ravn_index = i + 1
            peer_id = f"flock-{persona}"
            pub, rep, hs = _ports_for(ravn_index, base_port)
            gw = _gateway_port_for(ravn_index, base_port)

            config_yaml = _build_ravn_config(
                persona=persona,
                persona_override=persona_dict,
                global_llm=global_llm,
                index=ravn_index,
                peer_id=peer_id,
                base_port=base_port,
                all_personas=all_personas,
                skuld_peer_id=skuld_peer_id,
                mimir_config=mimir_config,
                sleipnir_publish_urls=sleipnir_publish_urls,
                global_max_concurrent_tasks=global_max_concurrent_tasks,
                mesh_host=self._mesh_host,
                persona_source_mode=persona_source_mode,
                persona_source_mount_path=persona_source_mount_path,
                persona_source_http_base_url=persona_source_http_base_url,
                workflow=workflow,
            )

            # Per-sidecar emptyDir volume for the mounted config file
            cfg_vol_name = f"{_RAVN_CONFIG_VOLUME_PREFIX}-{persona}"
            config_volumes.append({"name": cfg_vol_name, "emptyDir": {}})

            # Init container writes YAML into the volume
            heredoc = (
                f"cat > {_RAVN_CONFIG_MOUNT_PATH} <<'__RAVN_EOF__'\n{config_yaml}__RAVN_EOF__\n"
            )
            init_containers.append(
                {
                    "name": f"write-ravn-cfg-{persona}",
                    "image": _INIT_WRITER_IMAGE,
                    "command": ["sh", "-c", heredoc],
                    "volumeMounts": [
                        {"name": cfg_vol_name, "mountPath": _RAVN_CONFIG_DIR},
                    ],
                }
            )

            ravn_env: list[dict] = [
                {"name": "RAVN_PERSONA", "value": persona},
                {"name": "RAVN_PEER_ID", "value": peer_id},
                {"name": "RAVN_CONFIG", "value": _RAVN_CONFIG_MOUNT_PATH},
            ]

            if sleipnir_publish_urls:
                ravn_env.append(
                    {
                        "name": "SLEIPNIR_PUBLISH_URLS",
                        "value": ",".join(sleipnir_publish_urls),
                    }
                )

            ravn_env.extend(persona_source_envs)

            volume_mounts: list[dict] = [
                {
                    "name": _WORKSPACE_VOLUME_NAME,
                    "mountPath": _WORKSPACE_MOUNT_PATH,
                    "readOnly": True,
                },
                {
                    "name": cfg_vol_name,
                    "mountPath": _RAVN_CONFIG_DIR,
                    "readOnly": True,
                },
            ]
            if requires_local_mimir_mount:
                volume_mounts.insert(
                    0,
                    {"name": _MIMIR_VOLUME_NAME, "mountPath": _MIMIR_MOUNT_PATH},
                )
            volume_mounts.extend(persona_source_volume_mounts)

            container: dict[str, Any] = {
                "name": f"ravn-{persona}",
                "image": self._ravn_image,
                "env": ravn_env,
                "ports": [
                    {"containerPort": pub, "name": f"r{ravn_index}-pub", "protocol": "TCP"},
                    {"containerPort": rep, "name": f"r{ravn_index}-rep", "protocol": "TCP"},
                    {"containerPort": hs, "name": f"r{ravn_index}-hs", "protocol": "TCP"},
                    {"containerPort": gw, "name": f"r{ravn_index}-gw", "protocol": "TCP"},
                ],
                "volumeMounts": volume_mounts,
            }
            extra_containers.append(container)

        pod_volumes: list[dict[str, Any]] = [*config_volumes, *persona_source_volumes]
        if requires_local_mimir_mount:
            pod_volumes.insert(0, {"name": _MIMIR_VOLUME_NAME, "emptyDir": {}})

        pod_spec = PodSpecAdditions(
            volumes=tuple(pod_volumes),
            env=tuple(skuld_env),
            extra_containers=tuple(extra_containers),
            init_containers=tuple(init_containers),
        )

        values: dict[str, Any] = {
            "mesh": {
                "enabled": True,
                "transport": mesh_transport,
                "peerPorts": skuld_ports,
            },
            "flock": {
                "personas": persona_dicts,
                "llm_config": global_llm or {},
                "max_concurrent_tasks": global_max_concurrent_tasks,
            },
        }
        if workflow:
            values["workflow"] = workflow

        if mimir_config:
            values["mimir"] = {
                "hostedUrl": str(mimir_config.get("hosted_url") or ""),
                "registryRefs": list(mimir_config.get("registry_refs") or []),
                "ephemeralLocals": list(mimir_config.get("ephemeral_locals") or []),
                "bindings": list(mimir_config.get("bindings") or []),
                "instances": list(mimir_config.get("instances") or []),
                "writeRouting": dict(mimir_config.get("write_routing") or {}),
            }

        if sleipnir_publish_urls:
            values["sleipnir"] = {"publishUrls": sleipnir_publish_urls}

        logger.info(
            "ravn_flock: session=%s skuld peer=%s ravn personas=%s base_port=%d",
            session_id[:8],
            skuld_peer_id,
            all_personas,
            base_port,
        )

        return values, pod_spec
