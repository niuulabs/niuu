"""Direct Kubernetes API adapter for pod management.

Creates Deployment + Service + Ingress resources directly via the
kubernetes-asyncio Python client. Designed for local k3s/k3d development
where Flux is not available.

Constructor accepts plain kwargs (dynamic adapter pattern):
    adapter: "volundr.adapters.outbound.direct_k8s_pod_manager.DirectK8sPodManager"
    namespace: "volundr"
    kubeconfig: "~/.kube/config"
    base_path: "/s"
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import posixpath
import re
import shlex
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.brokered_credentials import BrokeredCredentialPodManager
from volundr.adapters.outbound.hosted_member_harness import (
    DEFAULT_POLL_SECONDS,
    DEFAULT_REQUEST_SECONDS,
    DEFAULT_RESTART_WAIT_SECONDS,
    MemberHarnessControl,
)
from volundr.adapters.outbound.local_process import (
    FORGE_MCP_GRANTS_ENV,
    FORGE_MCP_TOKEN_ENV,
    FORGE_MCP_TOKEN_FILE_ENV,
)
from volundr.adapters.outbound.mesh_settings import (
    mesh_environment,
    mesh_is_nng,
    mesh_lists_pods_of,
    mesh_with_defaults,
    parse_mesh_settings,
)
from volundr.domain.models import HOSTED_MEMBERS_KEY as UNIT_MEMBERS_KEY
from volundr.domain.models import Session, SessionSpec, SessionStatus
from volundr.domain.ports import (
    CredentialStorePort,
    HostedMemberLaunch,
    HostedUnitError,
    PodManager,
    PodStartResult,
)
from volundr.domain.services.forge_session_launch import launch_id_of
from volundr.domain.workflow_session import workflow_steps_environment

logger = logging.getLogger(__name__)

# Label applied to all resources managed by this adapter
MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
MANAGED_BY_VALUE = "volundr"
SESSION_LABEL = "volundr.niuu.io/session"

# Traefik strip-prefix middleware annotation key
TRAEFIK_STRIP_PREFIX_ANNOTATION = "traefik.ingress.kubernetes.io/router.middlewares"

# Service port for the session pod (nginx entry point)
SESSION_SERVICE_PORT = 8080

# The root's Skuld broker port in the pod (nginx's ``broker`` upstream).
SKULD_BROKER_PORT = 8081

#: Annotation of a unit's Deployment: the members its pod was started with, and the launch each
#: container runs under (``[{"id", "port", "launch"}]``).
MEMBERS_ANNOTATION = "volundr.niuu.io/members"

#: Where a unit's Skulds share their local mesh: one ``emptyDir`` mounted at this path.
DEFAULT_UNIT_MESH_DIR = "/tmp/niuu-mesh"

#: Values that belong to the pod, which the root alone decides. A member is a container of
#: that pod, so a member launched with a different one cannot be honoured, and the unit
#: refuses it rather than running the member somewhere it did not ask for.
_POD_LEVEL_VALUES: tuple[str, ...] = (
    "nodeSelector",
    "tolerations",
    "runtimeClassName",
    "homeVolume",
)

#: What a member's launch may not add to the pod beyond its own container.
_MEMBER_POD_ADDITIONS: tuple[str, ...] = ("init_containers", "extra_containers", "service_account")

#: Reasons a container waits that no amount of waiting will end.
_FAILED_WAITING_REASONS = frozenset(
    {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError"}
)

# Default poll interval for readiness checks in seconds
DEFAULT_POLL_INTERVAL = 2.0

# Default timeout for readiness checks in seconds
DEFAULT_READINESS_TIMEOUT = 300.0

# Nginx config template for the session pod entry point.
# Routes requests to skuld broker (8081), vscode-reh (8445), and terminal (7681).
NGINX_CONF_TEMPLATE = """\
worker_processes auto;
error_log /dev/stderr warn;
pid /tmp/nginx.pid;

events {{
    worker_connections 256;
}}

http {{
    access_log /dev/stdout;

    client_body_temp_path /tmp/client_temp;
    proxy_temp_path /tmp/proxy_temp;
    fastcgi_temp_path /tmp/fastcgi_temp;
    uwsgi_temp_path /tmp/uwsgi_temp;
    scgi_temp_path /tmp/scgi_temp;

    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_connect_timeout 10s;

    client_max_body_size 100m;

    upstream broker {{
        server 127.0.0.1:8081;
    }}

    upstream reh {{
        server 127.0.0.1:8445;
    }}
{member_upstreams}
    server {{
        listen 8080;
        server_name _;

        # Broker: WebSocket session endpoint
        location = /session {{
            proxy_pass http://broker/session;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }}
        location /session/ {{
            proxy_pass http://broker/session/;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }}

        # Broker: REST API endpoints
        location = /api {{
            return 301 /api/;
        }}
        location /api/ {{
            proxy_pass http://broker/api/;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        }}

        # Broker: health and readiness (for probes)
        location /health {{
            proxy_pass http://broker/health;
        }}
        location /ready {{
            proxy_pass http://broker/ready;
        }}

        # Terminal: WebSocket PTY
        location = /terminal {{
            return 301 /terminal/;
        }}
        location /terminal/ {{
            proxy_pass http://127.0.0.1:7681/;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }}

        # Dynamic local services - config generated by devrunner
        include {services_conf_path};

        # Service listing fallback
        location = /svc {{
            return 301 /svc/;
        }}
        location /svc/ {{
            default_type application/json;
            return 200 '{{"services":[],"hint":"use svc start to add services"}}';
        }}

        # VS Code REH: WebSocket RPC for editor workbench
        location /reh/ {{
            proxy_pass http://reh/;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }}

{member_locations}
        # Default catch-all -> broker
        location / {{
            proxy_pass http://broker/;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header Accept-Encoding gzip;
        }}
    }}
}}
"""


#: One member's upstream and route through the unit pod's nginx: its own Skuld, WebSocket
#: included. ``$MEMBER`` and ``$PORT`` are replaced; the text is inserted after the template
#: is formatted, so its braces are literal.
_MEMBER_UPSTREAM = """
    upstream member_$PORT {
        server 127.0.0.1:$PORT;
    }
"""
_MEMBER_LOCATION = """
        # Member $MEMBER
        location /m/$MEMBER/ {
            proxy_pass http://member_$PORT/;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
"""

_FORGE_MCP_ENV_NAMES = frozenset(
    {FORGE_MCP_TOKEN_ENV, FORGE_MCP_GRANTS_ENV, FORGE_MCP_TOKEN_FILE_ENV}
)

#: What a member's container sets for itself after the common environment, so the common
#: value is dropped.
_MEMBER_OWN_ENV = frozenset(
    {
        "SKULD__PORT",
        "SKULD__HOST",
        "SKULD__SESSION__WORKSPACE_DIR",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
    }
)


#: Where a pod's nng PUB socket listens, and the port k8s discovery dials it on.
DEFAULT_MESH_NNG_PORT = 7480
#: The pod's own IP, from the downward API: the host other pods dial it at.
POD_IP_ENV = "POD_IP"


class MeshDiscoveryRbacError(RuntimeError):
    """Forge cannot give a session's mesh the permission it needs to list pods."""


class DirectK8sPodManager(BrokeredCredentialPodManager, PodManager):
    """Direct Kubernetes API implementation of PodManager.

    Creates Deployment + Service + Ingress resources for each session
    using path-based routing without a host field. Suitable for local
    k3s/k3d development where only an IP (no DNS) is available.

    Constructor accepts plain kwargs (dynamic adapter pattern).
    """

    #: Takes Skuld's ``mesh`` settings and hands them to every session (``mesh_settings``).
    carries_mesh_setting = True

    def __init__(
        self,
        *,
        namespace: str = "volundr",
        kubeconfig: str = "",
        base_path: str = "/s",
        ingress_class: str = "traefik",
        ingress_backend: str = "k3d-volundr-serverlb",
        external_url: str = "http://127.0.0.1:8080",
        skuld_image: str = "ghcr.io/niuulabs/skuld:0.2.0",
        reh_image: str = "ghcr.io/niuulabs/vscode-reh:latest",
        nginx_image: str = "nginx:alpine",
        devrunner_image: str = "ghcr.io/niuulabs/devrunner:0.2.0",
        db_host: str = "host.k3d.internal",
        db_port: int = 5433,
        db_user: str = "volundr",
        db_password: str = "",
        db_name: str = "volundr",
        storage_path: str = "",
        home_mount_path: str = "/volundr/home",
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        readiness_timeout: float = DEFAULT_READINESS_TIMEOUT,
        codex_auth_adapter: str = "skuld.codex_auth.VolundrCodexAuthProvider",
        codex_auth_kwargs: dict | None = None,
        volundr_api_url: str = "",
        mesh: dict | str | None = None,
        mesh_nng_port: int = DEFAULT_MESH_NNG_PORT,
        unit_mesh_dir: str = DEFAULT_UNIT_MESH_DIR,
        hosted_member_restart_wait_seconds: float = DEFAULT_RESTART_WAIT_SECONDS,
        hosted_member_poll_seconds: float = DEFAULT_POLL_SECONDS,
        hosted_member_request_seconds: float = DEFAULT_REQUEST_SECONDS,
        **_extra: object,
    ):
        """``volundr_api_url`` is where a session pod reaches this Forge (the cluster-side
        name of the host, e.g. ``http://host.k3d.internal:8080``); a launch spec's
        ``volundr.apiUrl`` overrides it. A session whose Forge credential is mounted
        needs it (the broker asks that Forge for its mesh certificate), so a launch
        that has one and no URL is refused."""
        self._namespace = namespace
        self._kubeconfig = kubeconfig
        self._harness = MemberHarnessControl(
            restart_wait_seconds=hosted_member_restart_wait_seconds,
            poll_seconds=hosted_member_poll_seconds,
            request_seconds=hosted_member_request_seconds,
        )
        self._base_path = base_path
        self._ingress_class = ingress_class
        self._ingress_backend = ingress_backend
        self._external_url = external_url.rstrip("/")
        self._volundr_api_url = str(volundr_api_url).strip().rstrip("/")
        # Pods find each other by listing the session pods of the realm (k8s discovery) and
        # dial the PUB socket every pod opens on its own IP; the operator only enables the mesh.
        # The pod of a workflow run finds its own Skulds in a registry file instead.
        self._operator_mesh = parse_mesh_settings(mesh)
        self._unit_mesh_dir = unit_mesh_dir.rstrip("/")
        self._mesh = mesh_with_defaults(
            self._operator_mesh,
            {
                "nng": {
                    "pub_sub_address": f"tcp://0.0.0.0:{mesh_nng_port}",
                    "advertise_host": f"$({POD_IP_ENV})",
                },
                "discovery_adapters": [
                    {"adapter": "k8s", "namespace": namespace, "nng_port": mesh_nng_port}
                ],
            },
        )
        self._skuld_image = skuld_image
        self._reh_image = reh_image
        self._nginx_image = nginx_image
        self._devrunner_image = devrunner_image
        self._db_host = db_host
        self._db_port = db_port
        self._db_user = db_user
        self._db_password = db_password
        self._db_name = db_name
        self._storage_path = storage_path
        self._home_mount_path = home_mount_path
        self._poll_interval = poll_interval
        self._readiness_timeout = readiness_timeout
        self._configure_brokered_credentials(
            codex_auth_adapter=codex_auth_adapter,
            codex_auth_kwargs=codex_auth_kwargs,
        )
        self._credential_store: CredentialStorePort | None = None
        self._api_client = None
        self._member_failures: dict[str, str] = {}

    def set_credential_store(self, store: CredentialStorePort) -> None:
        """Inject credential store for resolving envSecrets."""
        self._credential_store = store

    async def _ensure_client(self) -> None:
        """Lazy-load kubernetes-asyncio API client."""
        if self._api_client is not None:
            return

        from kubernetes_asyncio import client, config

        if self._kubeconfig:
            await config.load_kube_config(config_file=self._kubeconfig)
        else:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                await config.load_kube_config()

        self._api_client = client.ApiClient()

    def _release_name(self, session: Session) -> str:
        """Generate the Kubernetes resource name for a session (a member's is its unit's)."""
        return f"skuld-{session.hosted_by or session.id}"

    def _session_path(self, session: Session) -> str:
        """Generate the ingress path for a session."""
        return f"{self._base_path}/{session.id}"

    def _middleware_name(self, session: Session) -> str:
        """Generate the Traefik middleware name for strip-prefix."""
        return f"{self._namespace}-strip-{session.id}"

    def _chat_endpoint(self, session: Session) -> str:
        """Build the chat WebSocket endpoint URL.

        Uses the ingress backend (Docker-internal) so the API server can proxy
        to the session pod. The CLI proxy rewrites these URLs in API responses
        before they reach the browser. A member of a run's unit is reached through
        its unit's path, which the pod's nginx routes to the member's own Skuld.
        """
        if session.hosted_by is not None:
            return f"ws://{self._ingress_backend}{self._member_service_path(session)}/session"
        return f"ws://{self._ingress_backend}{self._session_path(session)}/session"

    def _code_endpoint(self, session: Session) -> str:
        """Build the editor endpoint URL."""
        return f"http://{self._ingress_backend}{self._session_path(session)}/"

    def initial_code_endpoint(self, session: Session) -> str | None:
        return self._code_endpoint(session)

    def _build_labels(self, session: Session) -> dict[str, str]:
        """Build standard labels for Kubernetes resources."""
        return {
            MANAGED_BY_LABEL: MANAGED_BY_VALUE,
            SESSION_LABEL: str(session.id),
            "app.kubernetes.io/name": "skuld",
            "app.kubernetes.io/instance": self._release_name(session),
        }

    def _build_env(
        self, session: Session, spec: SessionSpec, mesh: dict | None = None
    ) -> list[dict[str, str]]:
        """Build environment variables for the skuld broker container.

        ``mesh`` replaces the pod manager's mesh settings (a unit's Skulds use their pod's own).
        """
        env: list[dict[str, str]] = [
            {"name": "SESSION_ID", "value": str(session.id)},
            {"name": "SESSION_NAME", "value": session.name},
            # Session ownership — the broker enforces these on inbound
            # WebSocket connections (skuld.config.WsAuthConfig).
            *(
                [{"name": "SKULD__SESSION__OWNER_ID", "value": session.owner_id}]
                if session.owner_id
                else []
            ),
            *(
                [{"name": "SKULD__SESSION__TENANT_ID", "value": session.tenant_id}]
                if session.tenant_id
                else []
            ),
            {"name": "DATABASE__HOST", "value": self._db_host},
            {"name": "DATABASE__PORT", "value": str(self._db_port)},
            {"name": "DATABASE__USER", "value": self._db_user},
            {"name": "DATABASE__PASSWORD", "value": self._db_password},
            {"name": "DATABASE__NAME", "value": self._db_name},
        ]
        env.extend(
            {"name": name, "value": value}
            for name, value in self._brokered_credential_environment(spec).items()
        )
        mesh_env = mesh_environment(self._mesh if mesh is None else mesh)
        if any(f"$({POD_IP_ENV})" in value for value in mesh_env.values()):
            # Defined before the entries that refer to it, which Kubernetes expands.
            env.append(
                {
                    "name": POD_IP_ENV,
                    "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}},
                }
            )
        env.extend({"name": name, "value": value} for name, value in mesh_env.items())

        # Handle git config from spec values.
        git_config = spec.values.get("git", {})
        if git_config.get("cloneUrl"):
            env.append({"name": "GIT_CLONE_URL", "value": git_config["cloneUrl"]})
        if git_config.get("branch"):
            env.append({"name": "GIT_BRANCH", "value": git_config["branch"]})

        # Handle session metadata from spec values.
        # Emit SKULD__SESSION__MODEL for pydantic-settings and MODEL for legacy fallback.
        session_config = spec.values.get("session", {})
        if session_config.get("model"):
            env.append({"name": "SKULD__SESSION__MODEL", "value": session_config["model"]})
            env.append({"name": "MODEL", "value": session_config["model"]})

        if session_config.get("systemPrompt"):
            env.append(
                {"name": "SKULD__SESSION__SYSTEM_PROMPT", "value": session_config["systemPrompt"]}
            )
        if session_config.get("persona"):
            env.append(
                {
                    "name": "SKULD__SESSION__PERSONA",
                    "value": json.dumps(session_config["persona"], separators=(",", ":")),
                }
            )
        env.extend(
            {"name": name, "value": value}
            for name, value in workflow_steps_environment(spec.values).items()
        )
        if session_config.get("reasoningEffort"):
            env.append(
                {
                    "name": "SKULD__SESSION__REASONING_EFFORT",
                    "value": session_config["reasoningEffort"],
                }
            )
        if session_config.get("initialPrompt"):
            env.append(
                {"name": "SKULD__SESSION__INITIAL_PROMPT", "value": session_config["initialPrompt"]}
            )

        # Handle extra env passthrough.
        extra_env = spec.values.get("env", {})
        if isinstance(extra_env, dict):
            for k, v in extra_env.items():
                env.append({"name": k, "value": str(v)})

        broker_config = spec.values.get("broker", {})
        if isinstance(broker_config, dict):
            if cli_type := broker_config.get("cliType"):
                env.append({"name": "SKULD__CLI_TYPE", "value": str(cli_type)})

            if transport := broker_config.get("transport"):
                env.append({"name": "SKULD__TRANSPORT", "value": str(transport)})

            if transport_adapter := broker_config.get("transportAdapter"):
                env.append(
                    {
                        "name": "SKULD__TRANSPORT_ADAPTER",
                        "value": str(transport_adapter),
                    }
                )

            if "skipPermissions" in broker_config:
                env.append(
                    {
                        "name": "SKULD__SKIP_PERMISSIONS",
                        "value": str(bool(broker_config["skipPermissions"])).lower(),
                    }
                )

            approval_policy = broker_config.get(
                "approvalPolicy",
                broker_config.get("approval_policy"),
            )
            if approval_policy:
                env.append({"name": "SKULD__APPROVAL_POLICY", "value": str(approval_policy)})

            if sandbox := broker_config.get("sandbox"):
                env.append({"name": "SKULD__SANDBOX", "value": str(sandbox)})

            # Pods do not read the node's config file: a definition carries the node's mode.
            claude_permission_mode = broker_config.get(
                "claudePermissionMode", broker_config.get("claude_permission_mode")
            )
            if claude_permission_mode:
                env.append(
                    {"name": "SKULD__CLAUDE_PERMISSION_MODE", "value": str(claude_permission_mode)}
                )

            if "agentTeams" in broker_config:
                env.append(
                    {
                        "name": "SKULD__AGENT_TEAMS",
                        "value": str(bool(broker_config["agentTeams"])).lower(),
                    }
                )

            telegram_cfg = broker_config.get("telegram")
            if isinstance(telegram_cfg, dict):
                if "enabled" in telegram_cfg:
                    env.append(
                        {
                            "name": "SKULD__TELEGRAM__ENABLED",
                            "value": str(bool(telegram_cfg["enabled"])).lower(),
                        }
                    )

                bot_token = telegram_cfg.get("botToken", telegram_cfg.get("bot_token"))
                if bot_token:
                    env.append(
                        {
                            "name": "SKULD__TELEGRAM__BOT_TOKEN",
                            "value": str(bot_token),
                        }
                    )

                chat_id = telegram_cfg.get("chatId", telegram_cfg.get("chat_id"))
                if chat_id:
                    env.append(
                        {
                            "name": "SKULD__TELEGRAM__CHAT_ID",
                            "value": str(chat_id),
                        }
                    )

                if "notifyOnly" in telegram_cfg or "notify_only" in telegram_cfg:
                    notify_only = telegram_cfg.get("notifyOnly", telegram_cfg.get("notify_only"))
                    env.append(
                        {
                            "name": "SKULD__TELEGRAM__NOTIFY_ONLY",
                            "value": str(bool(notify_only)).lower(),
                        }
                    )

                topic_mode = telegram_cfg.get("topicMode", telegram_cfg.get("topic_mode"))
                if topic_mode:
                    env.append(
                        {
                            "name": "SKULD__TELEGRAM__TOPIC_MODE",
                            "value": str(topic_mode),
                        }
                    )

                message_thread_id = telegram_cfg.get(
                    "messageThreadId",
                    telegram_cfg.get("message_thread_id"),
                )
                if message_thread_id not in (None, ""):
                    env.append(
                        {
                            "name": "SKULD__TELEGRAM__MESSAGE_THREAD_ID",
                            "value": str(message_thread_id),
                        }
                    )

        mcp_servers = spec.values.get("mcpServers")
        if isinstance(mcp_servers, list) and mcp_servers:
            env.append(
                {
                    "name": "SKULD__MCP_SERVERS",
                    "value": json.dumps(mcp_servers),
                }
            )

        if spec.pod_spec and spec.pod_spec.env:
            env.extend(spec.pod_spec.env)

        # Inject git credentials into the skuld container so that
        # git push / gh CLI work with the same token used for cloning.
        git_secret = git_config.get("secretName", "github-token")
        git_secret_key = git_config.get("secretKey", "token")
        if git_config.get("cloneUrl") and not git_config.get("credentials", {}).get("tokenFile"):
            for var_name in ("GITHUB_TOKEN", "GH_TOKEN"):
                env.append(
                    {
                        "name": var_name,
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": git_secret,
                                "key": git_secret_key,
                                "optional": True,
                            },
                        },
                    }
                )

        # Inject secrets from contributor-provided envSecrets.
        for es in spec.values.get("envSecrets", []):
            env.append(
                {
                    "name": es["envVar"],
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": es["secretName"],
                            "key": es["secretKey"],
                            "optional": True,
                        },
                    },
                }
            )

        # Nothing but this launch's mounted credential may name a session credential.
        env = [e for e in env if e.get("name") not in _FORGE_MCP_ENV_NAMES]
        env.extend(self._forge_env(spec))
        return env

    def _forge_env(self, spec: SessionSpec) -> list[dict[str, str]]:
        """Where the broker reaches this Forge, and the mounted session credential.

        Only the credential's file path and grants are env: the token itself is in
        the Secret the projection mounts. Applied last, so neither the launch spec's
        env nor a contributor can name a credential this launch did not mount.
        """
        forge_mcp = spec.values.get("forgeMcp") or {}
        token_file = str(forge_mcp.get("tokenFile") or "")
        api_url = str(
            (spec.values.get("volundr") or {}).get("apiUrl") or self._volundr_api_url
        ).rstrip("/")
        if token_file and not api_url:
            raise ValueError(
                "A Forge session credential is mounted into this session, but its broker "
                "has no Forge URL to use it against; set volundr_api_url on the "
                "DirectK8sPodManager (the address session pods reach Forge at), or set "
                "forge_mcp.session_tokens.enabled: false"
            )
        env: list[dict[str, str]] = []
        if api_url:
            env.append({"name": "SKULD__VOLUNDR_API_URL", "value": api_url})
        if token_file:
            grants = ",".join(str(grant) for grant in forge_mcp.get("grants") or [])
            env.append({"name": FORGE_MCP_TOKEN_FILE_ENV, "value": token_file})
            env.append({"name": FORGE_MCP_GRANTS_ENV, "value": grants})
        return env

    @staticmethod
    def _forge_session_mount_path(spec: SessionSpec) -> str:
        """The directory the session credential is mounted at, or empty."""
        token_file = str((spec.values.get("forgeMcp") or {}).get("tokenFile") or "")
        return posixpath.dirname(token_file) if token_file else ""

    def _pod_mounts(self, spec: SessionSpec) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Contributor volume mounts, split into those every container gets and the
        session credential's, which only the broker container may hold (the editor and
        terminal containers run the user's code)."""
        mounts = list(spec.pod_spec.volume_mounts) if spec.pod_spec else []
        credential_path = self._forge_session_mount_path(spec)
        shared = [m for m in mounts if not credential_path or m.get("mountPath") != credential_path]
        broker_only = [
            m for m in mounts if credential_path and m.get("mountPath") == credential_path
        ]
        return shared, broker_only

    def _build_workspace_volume(self, session: Session) -> dict[str, Any]:
        """Build the workspace volume spec.

        Uses a hostPath volume under storage_path when configured (for k3d
        with a host directory mounted into the nodes). Falls back to emptyDir.

        Mounts the root storage path so skuld can create its own session
        subdirectories under /volundr/sessions/{id}/workspace.
        """
        if not self._storage_path:
            return {"name": "workspace", "emptyDir": {}}

        return {
            "name": "workspace",
            "hostPath": {
                "path": self._storage_path,
                "type": "DirectoryOrCreate",
            },
        }

    def _build_home_volume(self, claim: str) -> dict[str, Any]:
        """Build the home volume spec.

        When the claim is an absolute path (from LocalStorageAdapter), uses
        a hostPath volume.  Otherwise treats it as a PVC claimName.
        """
        if claim.startswith("/"):
            return {
                "name": "home",
                "hostPath": {
                    "path": claim,
                    "type": "DirectoryOrCreate",
                },
            }

        return {
            "name": "home",
            "persistentVolumeClaim": {"claimName": claim},
        }

    def _build_init_containers(
        self,
        session: Session,
        spec: SessionSpec,
    ) -> list[dict[str, Any]]:
        """Build init containers: permissions fix, optional home-setup, optional git clone.

        The permissions step also pre-creates the empty devrunner services include:
        nginx loads it at startup, and devrunner only writes it once its own image
        has been pulled, so without it nginx crash-loops and delays readiness.
        """
        services_dir = shlex.quote(f"/volundr/sessions/{session.id}/workspace/.services")
        containers: list[dict[str, Any]] = [
            {
                "name": "init-permissions",
                "image": "busybox:latest",
                "command": [
                    "sh",
                    "-c",
                    (
                        f"mkdir -p {services_dir} || exit 1; "
                        f"touch {services_dir}/nginx.conf || exit 1; "
                        "chown -R 1000:1000 /volundr 2>/tmp/chown.err || true; "
                        "if [ -s /tmp/chown.err ]; then "
                        "grep -Ev 'Invalid argument|No such file or directory|Stale file handle' "
                        "/tmp/chown.err >&2; "
                        "if grep -qEv 'Invalid argument|No such file"
                        " or directory|Stale file handle' "
                        "/tmp/chown.err; then exit 1; fi; "
                        "fi"
                    ),
                ],
                "volumeMounts": [
                    {"name": "workspace", "mountPath": "/volundr"},
                ],
                "securityContext": {"runAsUser": 0},
            },
        ]

        git_config = spec.values.get("git", {})
        clone_url = git_config.get("cloneUrl", "")
        if not clone_url:
            return containers

        repo_url = git_config.get("repoUrl", clone_url)
        branch = git_config.get("branch", "main")
        base_branch = git_config.get("baseBranch", "")
        workspace = f"/volundr/sessions/{session.id}/workspace"

        credentials = git_config.get("credentials", {})
        token_file = credentials.get("tokenFile")
        if token_file:
            setup = f"""\
test -s {shlex.quote(token_file)} || {{
  echo "Selected Git integration token was not injected" >&2; exit 1;
}}
git -C "$WORKSPACE" config niuu.gitTokenFile {shlex.quote(token_file)}
git -C "$WORKSPACE" config niuu.gitUsername {shlex.quote(credentials["username"])}
git -C "$WORKSPACE" config credential.helper ''
git -C "$WORKSPACE" config credential.useHttpPath true
git -C "$WORKSPACE" config "credential.$REPO_URL.helper" '!f() {{
  [ "$1" = get ] || return 0
  token=$(cat "$(git config niuu.gitTokenFile)") || return 1
  [ -n "$token" ] || return 1
  printf "username=%s\\npassword=%s\\n" "$(git config niuu.gitUsername)" "$token"
}}; f'
"""
        else:
            setup = """\
git -C "$WORKSPACE" config credential.helper \
  '!f() { echo "username=x-access-token"; echo "password=$GITHUB_TOKEN"; }; f'
"""
        for key, value_key in (("name", "userName"), ("email", "userEmail")):
            if git_config.get(value_key):
                setup += (
                    f'if ! git -C "$WORKSPACE" config user.{key} >/dev/null; then\n'
                    f'  git -C "$WORKSPACE" config user.{key} '
                    f"{shlex.quote(git_config[value_key])}\nfi\n"
                )

        clone_script = f"""\
set -e
export GIT_TERMINAL_PROMPT=0
WORKSPACE={shlex.quote(workspace)}
CLONE_URL={shlex.quote(clone_url)}
REPO_URL={shlex.quote(repo_url)}
BRANCH={shlex.quote(branch)}
BASE_BRANCH={shlex.quote(base_branch)}
mkdir -p "$WORKSPACE"
if ! git -C "$WORKSPACE" rev-parse --git-dir >/dev/null 2>&1; then
  git init "$WORKSPACE"
fi
if git -C "$WORKSPACE" remote get-url origin >/dev/null 2>&1; then
  git -C "$WORKSPACE" remote set-url origin "$CLONE_URL"
else
  git -C "$WORKSPACE" remote add origin "$CLONE_URL"
fi
{setup}
if git -C "$WORKSPACE" rev-parse HEAD >/dev/null 2>&1; then
  echo "Workspace already contains a valid git repository, skipping clone"
  if [ -n "$BASE_BRANCH" ] && ! git -C "$WORKSPACE" config --get niuu.baseBranch >/dev/null; then
    git -C "$WORKSPACE" config niuu.baseBranch "$BASE_BRANCH"
  fi
else
  git -C "$WORKSPACE" fetch origin
  if git -C "$WORKSPACE" rev-parse --verify "origin/$BRANCH" >/dev/null 2>&1; then
    git -C "$WORKSPACE" checkout -B "$BRANCH" "origin/$BRANCH"
  else
    if [ -n "$BASE_BRANCH" ] && \
      git -C "$WORKSPACE" rev-parse --verify "origin/$BASE_BRANCH" >/dev/null 2>&1; then
      FALLBACK="$BASE_BRANCH"
    else
      FALLBACK=$(git -C "$WORKSPACE" remote show origin | sed -n 's/.*HEAD branch: //p')
      FALLBACK=${{FALLBACK:-main}}
    fi
    git -C "$WORKSPACE" checkout -B "$FALLBACK" "origin/$FALLBACK"
    git -C "$WORKSPACE" checkout -b "$BRANCH"
  fi
  # Record where this branch started, so the session's changes can be shown against it.
  RECORDED_BASE="${{FALLBACK:-}}"
  if [ -z "$RECORDED_BASE" ]; then
    if [ -n "$BASE_BRANCH" ] && \
      git -C "$WORKSPACE" rev-parse --verify "origin/$BASE_BRANCH" >/dev/null 2>&1; then
      RECORDED_BASE="$BASE_BRANCH"
    else
      RECORDED_BASE=$(git -C "$WORKSPACE" remote show origin | sed -n 's/.*HEAD branch: //p')
    fi
  fi
  if [ -n "$RECORDED_BASE" ]; then
    git -C "$WORKSPACE" config niuu.baseBranch "$RECORDED_BASE"
  fi
  echo "Repository cloned successfully"
fi
git -C "$WORKSPACE" remote set-url origin "$REPO_URL"
"""
        containers.append(
            {
                "name": "git-clone",
                "image": "alpine/git:latest",
                "command": ["/bin/sh", "-c"],
                "args": [clone_script],
                "env": [
                    {
                        "name": "GITHUB_TOKEN",
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": "github-token",
                                "key": "token",
                                "optional": True,
                            },
                        },
                    },
                ],
                "securityContext": {"runAsUser": 1000, "allowPrivilegeEscalation": False},
                "volumeMounts": [
                    {"name": "workspace", "mountPath": "/volundr"},
                ],
            }
        )

        if token_file:
            containers[-1]["env"] = []
        home = spec.values.get("homeVolume", {})
        if home.get("enabled"):
            home_path = home.get("mountPath", self._home_mount_path)
            containers[-1]["env"].append({"name": "HOME", "value": home_path})
            containers[-1]["volumeMounts"].append(
                {"name": "home", "mountPath": home_path, "readOnly": True}
            )
        if spec.pod_spec:
            containers[-1]["volumeMounts"].extend(self._pod_mounts(spec)[0])

        # Append contributor-provided init containers (e.g. credential writers).
        if spec.pod_spec:
            containers.extend(spec.pod_spec.init_containers)

        return containers

    def _build_deployment_manifest(
        self,
        session: Session,
        spec: SessionSpec,
        members: Sequence[HostedMemberLaunch] = (),
    ) -> dict[str, Any]:
        """Build a Kubernetes Deployment manifest dict for the session.

        A unit root's ``members`` become containers of the same pod (see the unit section).
        """
        labels = self._build_labels(session)
        release_name = self._release_name(session)
        mesh = self._unit_mesh(session) if members else None
        env_list = self._build_env(session, spec, mesh=mesh)

        env_vars = env_list

        # Merge contributor-provided volumes and volume mounts.
        extra_volumes = list(spec.pod_spec.volumes) if spec.pod_spec else []
        extra_mounts, broker_only_mounts = self._pod_mounts(spec)

        volumes = [
            self._build_workspace_volume(session),
            {
                "name": "nginx-config",
                "configMap": {"name": f"{release_name}-nginx"},
            },
            *extra_volumes,
        ]

        base_mounts = [{"name": "workspace", "mountPath": "/volundr"}]
        workload_mounts = base_mounts + extra_mounts

        # Extract resource overrides from contributor spec values.
        # These mirror the Helm chart values layout set by ResourceContributor.
        devrunner_resources = (
            spec.values.get("localServices", {}).get("devrunner", {}).get("resources", {})
        )

        # Default resources per container — overridden by user-specified values.
        nginx_res = {
            "requests": {"memory": "32Mi", "cpu": "10m"},
            "limits": {"memory": "128Mi", "cpu": "100m"},
        }
        skuld_res = self._skuld_resources(spec.values)
        reh_res = {
            "requests": {"memory": "256Mi", "cpu": "100m"},
            "limits": {"memory": "2Gi", "cpu": "1000m"},
        }
        devrunner_res = {
            "requests": {"memory": "512Mi", "cpu": "100m"},
            "limits": {"memory": "4Gi", "cpu": "2000m"},
        }

        # Apply user-specified devrunner overrides (CPU, memory, GPU)
        if devrunner_resources.get("requests"):
            devrunner_res["requests"].update(devrunner_resources["requests"])
        if devrunner_resources.get("limits"):
            devrunner_res["limits"].update(devrunner_resources["limits"])

        # Pod-level scheduling from contributor values
        node_selector = spec.values.get("nodeSelector", {})
        tolerations = spec.values.get("tolerations", [])
        runtime_class_name = spec.values.get("runtimeClassName")

        # Home volume from StorageContributor
        home_vol = spec.values.get("homeVolume", {})
        home_enabled = home_vol.get("enabled", False)
        home_mount = home_vol.get("mountPath", self._home_mount_path)
        home_claim = home_vol.get("existingClaim", "")

        if home_enabled and home_claim:
            home_volume = self._build_home_volume(home_claim)
            volumes.append(home_volume)
            workload_mounts = workload_mounts + [
                {"name": "home", "mountPath": home_mount},
            ]

        # HOME env var — set when home volume is mounted
        home_env: list[dict[str, str]] = []
        if home_enabled:
            home_env = [
                {"name": "HOME", "value": home_mount},
                {"name": "SKULD__PERSISTENT_HOME_PATH", "value": home_mount},
            ]

        member_containers: list[dict[str, Any]] = []
        mesh_mount: list[dict[str, Any]] = []
        if members:
            mesh_mount = [{"name": "mesh", "mountPath": self._unit_mesh_dir}]
            volumes.append({"name": "mesh", "emptyDir": {}})
            shared_mounts = [*base_mounts, *mesh_mount]
            if home_enabled and home_claim:
                shared_mounts.append({"name": "home", "mountPath": home_mount})
            for launch in members:
                container, own_volumes = self._member_container(
                    session,
                    spec,
                    launch,
                    mesh=mesh,
                    mounts=shared_mounts,
                    home_env=home_env,
                )
                member_containers.append(container)
                volumes.extend(own_volumes)

        safe_hostname = re.sub(r"[^a-z0-9-]", "-", session.name.lower())
        safe_hostname = safe_hostname.strip("-")[:63] or "session"
        pod_spec: dict[str, Any] = {
            "hostname": safe_hostname,
            "terminationGracePeriodSeconds": 30,
            **(
                {"serviceAccountName": spec.pod_spec.service_account}
                if spec.pod_spec and spec.pod_spec.service_account
                else {}
            ),
            "securityContext": {
                "fsGroup": 1000,
                "fsGroupChangePolicy": "OnRootMismatch",
            },
            "initContainers": self._build_init_containers(
                session, self._checkout_spec(session, spec, members)
            ),
            "volumes": volumes,
            "containers": [
                {
                    "name": "nginx",
                    "image": self._nginx_image,
                    "ports": [{"containerPort": SESSION_SERVICE_PORT, "name": "http"}],
                    "volumeMounts": [
                        {
                            "name": "nginx-config",
                            "mountPath": "/etc/nginx/nginx.conf",
                            "subPath": "nginx.conf",
                            "readOnly": True,
                        },
                        {"name": "workspace", "mountPath": "/volundr"},
                    ],
                    "resources": nginx_res,
                },
                {
                    "name": "skuld",
                    "image": self._skuld_image,
                    "ports": [{"containerPort": 8081, "name": "broker"}],
                    "env": env_vars + home_env,
                    "securityContext": {
                        "runAsUser": 1000,
                        "allowPrivilegeEscalation": False,
                    },
                    "volumeMounts": workload_mounts + broker_only_mounts + mesh_mount,
                    "resources": skuld_res,
                },
                *member_containers,
                {
                    "name": "vscode-reh",
                    "image": self._reh_image,
                    "ports": [{"containerPort": 8445, "name": "reh"}],
                    "env": [
                        {"name": "SESSION_ID", "value": str(session.id)},
                    ]
                    + home_env,
                    "securityContext": {
                        "runAsUser": 1000,
                        "allowPrivilegeEscalation": False,
                    },
                    "volumeMounts": workload_mounts,
                    "resources": reh_res,
                    "livenessProbe": {
                        "tcpSocket": {"port": 8445},
                        "initialDelaySeconds": 10,
                        "periodSeconds": 30,
                    },
                    "readinessProbe": {
                        "tcpSocket": {"port": 8445},
                        "initialDelaySeconds": 5,
                        "periodSeconds": 10,
                    },
                },
                {
                    "name": "devrunner",
                    "image": self._devrunner_image,
                    "env": [
                        {"name": "SESSION_ID", "value": str(session.id)},
                        {"name": "TERMINAL_PORT", "value": "7681"},
                    ]
                    + home_env,
                    "securityContext": {
                        "runAsUser": 1000,
                        "allowPrivilegeEscalation": False,
                    },
                    "volumeMounts": workload_mounts,
                    "resources": devrunner_res,
                },
            ],
        }

        if home_enabled and home_vol.get("persistentTmp"):
            self._add_persistent_scratch(pod_spec, session)

        if node_selector:
            pod_spec["nodeSelector"] = node_selector
        if tolerations:
            pod_spec["tolerations"] = tolerations
        if runtime_class_name:
            pod_spec["runtimeClassName"] = runtime_class_name

        annotations = dict(spec.pod_spec.annotations) if spec.pod_spec else {}
        inject_key = "vault.hashicorp.com/agent-inject-containers"
        if (
            spec.values.get("git", {}).get("credentials", {}).get("tokenFile")
            and inject_key in annotations
        ):
            names = annotations[inject_key].split(",")
            names.extend(c["name"] for c in pod_spec["containers"])
            names.append("git-clone")
            annotations[inject_key] = ",".join(dict.fromkeys(names))

        pod_labels, mesh_annotations = self._mesh_pod_identity(session, mesh)
        annotations.update(mesh_annotations)
        deployment_annotations = {}
        if members:
            deployment_annotations[MEMBERS_ANNOTATION] = json.dumps(
                [
                    {
                        "id": str(launch.session.id),
                        "port": launch.session.hosted_port,
                        "launch": launch_id_of(launch.session.workload_config) or "",
                    }
                    for launch in members
                ]
            )

        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": release_name,
                "namespace": self._namespace,
                "labels": labels,
                **({"annotations": deployment_annotations} if deployment_annotations else {}),
            },
            "spec": {
                "replicas": 1,
                "selector": {
                    "matchLabels": {
                        "app.kubernetes.io/instance": release_name,
                    },
                },
                "template": {
                    "metadata": {
                        "labels": {**labels, **pod_labels},
                        "annotations": annotations,
                    },
                    "spec": pod_spec,
                },
            },
        }

    def _mesh_rbac_name(self, session: Session) -> str:
        return f"{self._release_name(session)}-mesh-discovery"

    def _mesh_rbac_manifests(
        self, session: Session, spec: SessionSpec
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """The Role and RoleBinding k8s discovery needs, or ``None`` when the mesh lists no pods.

        Namespaced, and nothing but reading pods: the session's ServiceAccount (the one the
        pod runs as) may get, list and watch the pods of this namespace.
        """
        if not mesh_lists_pods_of(self._mesh, self._namespace):
            return None
        name = self._mesh_rbac_name(session)
        metadata = {
            "name": name,
            "namespace": self._namespace,
            "labels": self._build_labels(session),
        }
        service_account = (
            spec.pod_spec.service_account if spec.pod_spec and spec.pod_spec.service_account else ""
        ) or "default"
        role = {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": dict(metadata),
            "rules": [
                {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]}
            ],
        }
        binding = {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": dict(metadata),
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": name},
            "subjects": [
                {"kind": "ServiceAccount", "name": service_account, "namespace": self._namespace}
            ],
        }
        return role, binding

    async def _apply_mesh_rbac(self, session: Session, spec: SessionSpec) -> None:
        """Create the discovery Role and binding before the pod starts; refuse if Forge cannot."""
        manifests = self._mesh_rbac_manifests(session, spec)
        if manifests is None:
            return
        role, binding = manifests
        name = role["metadata"]["name"]
        try:
            for kind, manifest in (("role", role), ("role_binding", binding)):
                await self._apply_resource(
                    api_method_create=f"create_namespaced_{kind}",
                    api_method_patch=f"patch_namespaced_{kind}",
                    api_class="RbacAuthorizationV1Api",
                    name=name,
                    manifest=manifest,
                )
        except self._get_api_exception() as exc:
            if exc.status != 403:
                raise
            raise MeshDiscoveryRbacError(
                f"Forge may not create the Role and RoleBinding {name} in namespace "
                f"{self._namespace} that the session's mesh needs to list pods (HTTP 403). Grant "
                "its ServiceAccount create, get, patch and delete on roles and rolebindings "
                "there (the volundr chart does when its pod manager is the direct Kubernetes "
                "one), or give the mesh other discovery in mesh.discovery_adapters"
            ) from exc

    def _mesh_pod_identity(
        self, session: Session, mesh: dict | None = None
    ) -> tuple[dict[str, str], dict[str, str]]:
        """The labels and annotations k8s discovery reads: realm, peer id, kind and name."""
        mesh = self._mesh if mesh is None else mesh
        if not mesh_is_nng(mesh):
            return {}, {}
        adapters = mesh.get("discovery_adapters") or []
        if not any(entry.get("adapter") == "k8s" for entry in adapters):
            return {}, {}
        peer_id = str(mesh.get("peer_id") or session.id)
        return (
            {
                "ravn.niuu.world/realm": str(mesh.get("realm_id") or ""),
                "ravn.niuu.world/role": "agent",
            },
            {
                "ravn.niuu.world/peer-id": peer_id,
                "ravn.niuu.world/participant-type": "skuld",
                **({"ravn.niuu.world/display-name": session.name} if session.name else {}),
            },
        )

    @staticmethod
    def _add_persistent_scratch(pod_spec: dict[str, Any], session: Session) -> None:
        """Keep temporary files and reusable caches on the owner's home volume."""
        cache_env = {
            "TMPDIR": "/tmp",
            "XDG_CACHE_HOME": "/var/cache/niuu",
            "GOCACHE": "/var/cache/niuu/go-build",
            "GOMODCACHE": "/var/cache/niuu/go-mod",
            "npm_config_cache": "/var/cache/niuu/npm",
            "PIP_CACHE_DIR": "/var/cache/niuu/pip",
            "UV_CACHE_DIR": "/var/cache/niuu/uv",
        }
        session_tmp = shlex.quote(f"/home/tmp/sessions/{session.id}")
        setup = [
            "umask 007",
            f"mkdir -p /home/tmp/cache {session_tmp}",
            f"chown -h 1000:1000 /home/tmp /home/tmp/cache /home/tmp/sessions {session_tmp}",
        ]
        for container in pod_spec["containers"]:
            if container["name"] == "nginx":
                continue
            subpath = f"tmp/sessions/{session.id}/{container['name']}"
            mounts = list(container.get("volumeMounts", []))
            container["volumeMounts"] = mounts
            if any(m["mountPath"] in ("/tmp", "/var/cache/niuu") for m in mounts):
                raise ValueError("persistentTmp conflicts with an existing scratch mount")
            mounts.extend(
                [
                    {"name": "home", "mountPath": "/tmp", "subPath": subpath},
                    {"name": "home", "mountPath": "/var/cache/niuu", "subPath": "tmp/cache"},
                ]
            )
            env = container.setdefault("env", [])
            existing = {e["name"] for e in env}
            env.extend({"name": k, "value": v} for k, v in cache_env.items() if k not in existing)
            target = shlex.quote(f"/home/{subpath}")
            setup.extend(
                [f"mkdir -p {target}", f"chown -h 1000:1000 {target}", f"chmod 1777 {target}"]
            )
        # HostPath does not apply fsGroup. Only change ownership of the mount root.
        pod_spec["initContainers"].insert(
            0,
            {
                "name": "home-permissions",
                "image": "busybox:latest",
                "command": ["sh", "-ec", "chown 1000:1000 /home"],
                "securityContext": {"runAsUser": 0, "allowPrivilegeEscalation": False},
                "volumeMounts": [{"name": "home", "mountPath": "/home"}],
            },
        )
        pod_spec["initContainers"].insert(
            1,
            {
                "name": "scratch-setup",
                "image": "busybox:latest",
                "command": ["sh", "-ec", "\n".join(setup)],
                "securityContext": {
                    "runAsUser": 0,
                    "runAsNonRoot": False,
                    "allowPrivilegeEscalation": False,
                },
                "volumeMounts": [{"name": "home", "mountPath": "/home"}],
            },
        )

    def _build_service_manifest(self, session: Session) -> dict[str, Any]:
        """Build a Kubernetes Service manifest dict for the session."""
        labels = self._build_labels(session)
        release_name = self._release_name(session)

        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": release_name,
                "namespace": self._namespace,
                "labels": labels,
            },
            "spec": {
                "type": "ClusterIP",
                "selector": {
                    "app.kubernetes.io/instance": release_name,
                },
                "ports": [
                    {
                        "name": "http",
                        "port": SESSION_SERVICE_PORT,
                        "targetPort": SESSION_SERVICE_PORT,
                    },
                ],
            },
        }

    def _build_ingress_manifest(self, session: Session) -> dict[str, Any]:
        """Build a Kubernetes Ingress manifest with path-based routing.

        Uses Traefik strip-prefix annotation so the path prefix is
        removed before reaching the pod. No host field is set,
        allowing IP-based access.
        """
        labels = self._build_labels(session)
        release_name = self._release_name(session)
        session_path = self._session_path(session)
        middleware_ref = f"{self._namespace}-{release_name}-strip@kubernetescrd"

        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": release_name,
                "namespace": self._namespace,
                "labels": labels,
                "annotations": {
                    TRAEFIK_STRIP_PREFIX_ANNOTATION: middleware_ref,
                },
            },
            "spec": {
                "ingressClassName": self._ingress_class,
                "rules": [
                    {
                        "http": {
                            "paths": [
                                {
                                    "path": session_path,
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": release_name,
                                            "port": {"number": SESSION_SERVICE_PORT},
                                        },
                                    },
                                },
                            ],
                        },
                    },
                ],
            },
        }

    def _build_strip_prefix_middleware(self, session: Session) -> dict[str, Any]:
        """Build a Traefik Middleware CR for stripping the session path prefix."""
        release_name = self._release_name(session)
        session_path = self._session_path(session)

        return {
            "apiVersion": "traefik.io/v1alpha1",
            "kind": "Middleware",
            "metadata": {
                "name": f"{release_name}-strip",
                "namespace": self._namespace,
                "labels": self._build_labels(session),
            },
            "spec": {
                "stripPrefix": {
                    "prefixes": [session_path],
                },
            },
        }

    def _build_nginx_configmap(
        self, session: Session, members: Sequence[HostedMemberLaunch] = ()
    ) -> dict[str, Any]:
        """Build the nginx ConfigMap manifest for the session pod.

        A unit's pod also routes ``/m/<member id>/`` to each member's own Skuld.
        """
        release_name = self._release_name(session)
        services_conf_path = f"/volundr/sessions/{session.id}/workspace/.services/nginx.conf"
        upstreams = ""
        locations = ""
        for launch in members:
            ids = {"$MEMBER": str(launch.session.id), "$PORT": str(int(launch.session.hosted_port))}
            upstream, location = _MEMBER_UPSTREAM, _MEMBER_LOCATION
            for placeholder, value in ids.items():
                upstream = upstream.replace(placeholder, value)
                location = location.replace(placeholder, value)
            upstreams += upstream
            locations += location
        nginx_conf = NGINX_CONF_TEMPLATE.format(
            services_conf_path=services_conf_path,
            member_upstreams=upstreams,
            member_locations=locations,
        )
        return {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": f"{release_name}-nginx",
                "namespace": self._namespace,
                "labels": self._build_labels(session),
            },
            "data": {
                "nginx.conf": nginx_conf,
            },
        }

    # ------------------------------------------------------------------
    # A workflow run's unit: one pod, a container per member
    # ------------------------------------------------------------------
    #
    # The root's Deployment is the run's pod (docs/design/workflow-mesh-handoff.md,
    # section 2), built as the Flux pod is: a ``member-<port>`` Skuld container per member
    # beside the root's, its own port, its own environment and its own credential Secret
    # mounted into that container only. The Skulds share the root's checkout and a local
    # mesh (nng on loopback, a registry file in an ``emptyDir`` they all mount). nginx
    # routes ``/m/<member id>/`` to the member's port; every member starts with the pod.

    @staticmethod
    def _member_container_name(port: int) -> str:
        return f"member-{port}"

    def _unit_mesh(self, root: Session) -> dict:
        """The mesh of a unit: nng on loopback, discovered through the pod's registry file.

        Whatever the operator set stays as set; only the defaults change from "the pods of
        the namespace" to "the Skulds of this pod". A run needs the mesh, so it does not
        start with the mesh off.
        """
        enabled = self._operator_mesh.get("enabled")
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() == "true"
        if not enabled:
            raise ValueError(
                f"Unit {root.id} hosts a workflow run, whose members hand steps to each other "
                "over the mesh, but this pod manager's mesh is off: set `mesh.enabled: true` "
                "in its configuration"
            )
        return mesh_with_defaults(
            self._operator_mesh,
            {
                "nng": {
                    "pub_sub_address": "tcp://127.0.0.1:0",
                    "req_rep_address": "tcp://127.0.0.1:0",
                    "advertise_host": "",
                },
                "discovery_adapters": [
                    {"adapter": "registry", "path": f"{self._unit_mesh_dir}/peers.json"}
                ],
            },
        )

    @staticmethod
    def _require_members(root: Session, members: Sequence[HostedMemberLaunch]) -> None:
        """The members Forge launched are exactly the ones it recorded as the unit's.

        A unit root whose recorded members did not arrive would start as a pod with only the
        root's Skuld, and the run's members would never come up: refused instead.
        """
        recorded = {
            str(entry["id"]): int(entry["port"])
            for entry in root.workload_config.get(UNIT_MEMBERS_KEY) or []
        }
        if root.hosted_by is not None:
            raise ValueError(
                f"Session {root.id} is a member of unit {root.hosted_by}: a member starts "
                "with its unit, never on its own"
            )
        if not members:
            if recorded:
                raise ValueError(
                    f"Unit {root.id} was started with members {sorted(recorded)} but no "
                    "launch for them came from the member source: Forge "
                    "must render each member's spec before it starts the root"
                )
            return
        if not root.is_unit_root:
            raise ValueError(
                f"Session {root.id} was launched with {len(members)} member(s) but is not a "
                "unit root (created with hosts_members)"
            )
        launched: dict[str, int] = {}
        for launch in members:
            member = launch.session
            if member.hosted_by != root.id:
                raise ValueError(
                    f"Session {member.id} is launched in unit {root.id} but is hosted by "
                    f"{member.hosted_by}"
                )
            if member.hosted_port is None:
                raise ValueError(f"Member {member.id} of unit {root.id} has no broker port")
            if str(member.id) in launched:
                raise ValueError(f"Member {member.id} is launched twice in unit {root.id}")
            launched[str(member.id)] = member.hosted_port
        if launched != recorded:
            raise ValueError(
                f"Unit {root.id} recorded members {sorted(recorded.items())} when it started "
                f"but was launched with {sorted(launched.items())}: a run's members are fixed "
                "when its unit starts"
            )
        ports = list(launched.values())
        if len(set(ports)) != len(ports) or SKULD_BROKER_PORT in ports:
            raise ValueError(
                f"Unit {root.id} gives two Skulds the same broker port: members {ports} and "
                f"the root's {SKULD_BROKER_PORT}"
            )

    @staticmethod
    def _unit_git(root: Session, spec: SessionSpec, members: Sequence[HostedMemberLaunch]) -> dict:
        """The one repository and branch the unit's checkout is cloned from.

        The root's own wins when it names one; otherwise the members' must agree. A member
        that names another repository or branch is refused: it would otherwise work in a
        checkout of something it did not ask for. Returns the git values to clone with
        (empty when no one names a repository).
        """

        def wanted(git: dict) -> tuple[str, str, str]:
            return (
                str(git.get("repoUrl") or git.get("cloneUrl") or ""),
                str(git.get("branch") or "main"),
                str(git.get("baseBranch") or ""),
            )

        root_git = spec.values.get("git") or {}
        named = {
            str(launch.session.id): launch.spec.values.get("git") or {}
            for launch in members
            if (launch.spec.values.get("git") or {}).get("cloneUrl")
        }
        if root_git.get("cloneUrl"):
            for member_id, git in named.items():
                if wanted(git) != wanted(root_git):
                    raise ValueError(
                        f"Member {member_id} of unit {root.id} was launched on {wanted(git)} "
                        f"but the unit's one checkout is {wanted(root_git)}: a run's members "
                        "share a repository and branch"
                    )
            return root_git
        if len({wanted(git) for git in named.values()}) > 1:
            raise ValueError(
                f"Members of unit {root.id} were launched on different repositories or "
                f"branches {sorted({wanted(git) for git in named.values()})}: a run's members "
                "share one checkout"
            )
        return next(iter(named.values()), {})

    def _require_same_pod(
        self, root: Session, root_spec: SessionSpec, launch: HostedMemberLaunch
    ) -> None:
        """A member is a container of the root's pod: it cannot ask for another pod."""
        member = launch.session
        where = f"member {member.id} of unit {root.id}"
        additions = launch.spec.pod_spec
        for field in _MEMBER_POD_ADDITIONS:
            if additions is not None and getattr(additions, field):
                raise ValueError(
                    f"{where} asks for {field} of its own; a member is a container of its "
                    "unit's pod and cannot add pod-level parts"
                )
        for key in _POD_LEVEL_VALUES:
            wanted, unit = launch.spec.values.get(key), root_spec.values.get(key)
            if wanted != unit:
                raise ValueError(
                    f"{where} was launched with {key}={wanted!r} but its unit's pod has "
                    f"{unit!r}: members share one pod, so they must agree on it"
                )
        for kind in ("labels", "annotations"):
            unit_items = getattr(root_spec.pod_spec, kind) if root_spec.pod_spec else {}
            for name, value in (getattr(additions, kind) if additions else {}).items():
                if unit_items.get(name) != value:
                    raise ValueError(
                        f"{where} asks for pod {kind[:-1]} {name!r}={value!r}, which its "
                        "unit's pod does not have: members share one pod"
                    )

    def _member_container(
        self,
        root: Session,
        root_spec: SessionSpec,
        launch: HostedMemberLaunch,
        *,
        mesh: dict,
        mounts: list[dict[str, Any]],
        home_env: list[dict[str, str]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """One member's Skuld container, and the volumes only its mounts use.

        Its volumes (the credential Secret and whatever else its launch mounted) are the
        pod's, under names only this member's mounts refer to, so the credential is in this
        container and no other. ``mounts`` are the pod's shared ones (workspace, home, mesh).
        """
        member = launch.session
        port = int(member.hosted_port)
        self._require_same_pod(root, root_spec, launch)
        spec = self._with_brokered_credentials(launch.spec)
        prefix = f"member-{port}-"
        volumes = [
            {**volume, "name": f"{prefix}{volume['name']}"}
            for volume in (spec.pod_spec.volumes if spec.pod_spec else ())
        ]
        own_names = {f"{v['name']}": f"{prefix}{v['name']}" for v in spec.pod_spec.volumes}
        own_mounts = [
            {**mount, "name": own_names.get(mount["name"], mount["name"])}
            for mount in (spec.pod_spec.volume_mounts if spec.pod_spec else ())
        ]
        member_home = f"/volundr/sessions/{root.id}/members/{port}/home"
        env = self._build_env(member, spec, mesh=mesh)
        env = [e for e in env if e["name"] not in _MEMBER_OWN_ENV]
        env.extend(
            [
                {"name": "SKULD__PORT", "value": str(port)},
                {"name": "SKULD__HOST", "value": "127.0.0.1"},
                {
                    "name": "SKULD__SESSION__WORKSPACE_DIR",
                    "value": f"/volundr/sessions/{root.id}/workspace",
                },
                # The agent state of a member is its own (two members never share a Codex
                # database or a Claude session store); the checkout and HOME are the unit's.
                {"name": "CODEX_HOME", "value": f"{member_home}/.codex"},
                {"name": "CLAUDE_CONFIG_DIR", "value": f"{member_home}/.claude"},
            ]
        )
        container: dict[str, Any] = {
            "name": self._member_container_name(port),
            "image": self._skuld_image,
            "ports": [{"containerPort": port, "name": f"m{port}"}],
            "env": env + home_env,
            "securityContext": {"runAsUser": 1000, "allowPrivilegeEscalation": False},
            "volumeMounts": [*mounts, *own_mounts],
            "resources": self._skuld_resources(spec.values),
        }
        return container, volumes

    @staticmethod
    def _skuld_resources(values: dict) -> dict[str, dict[str, str]]:
        """The Skuld container's requests and limits: the defaults, then the launch's."""
        resources = {
            "requests": {"memory": "256Mi", "cpu": "100m"},
            "limits": {"memory": "1Gi", "cpu": "500m"},
        }
        configured = values.get("resources", {})
        for kind in ("requests", "limits"):
            if configured.get(kind):
                resources[kind].update(configured[kind])
        return resources

    def initial_chat_endpoint(self, session: Session) -> str | None:
        return self._chat_endpoint(session)

    def _checkout_spec(
        self, root: Session, spec: SessionSpec, members: Sequence[HostedMemberLaunch]
    ) -> SessionSpec:
        """``spec`` as the unit's one checkout is cloned from (see ``_unit_git``)."""
        if not members:
            return spec
        git = self._unit_git(root, spec, members)
        if not git or git == spec.values.get("git"):
            return spec
        return dataclasses.replace(spec, values={**spec.values, "git": git})

    def _member_service_path(self, member: Session) -> str:
        """Where the unit's ingress path leads to a member: the pod's nginx routes it."""
        return f"{self._base_path}/{member.hosted_by}/m/{member.id}"

    async def broker_target(self, session: Session) -> SessionProxyTarget | None:
        """Where Forge reaches a member's broker: its path on the unit's ingress.

        A member's chat endpoint carries the unit's address and the member's port, which the
        pod's single entry point does not serve. An ordinary session keeps its chat endpoint.
        """
        if session.hosted_by is None:
            return None
        base = f"http://{self._ingress_backend}{self._member_service_path(session)}"
        parts = urlsplit(base)
        if not parts.hostname:
            raise ValueError(f"The ingress backend {self._ingress_backend!r} has no host")
        return SessionProxyTarget(
            service_url=base,
            connect_host=parts.hostname,
            connect_port=parts.port or 80,
        )

    async def stop_hosted_member(self, member: Session) -> None:
        """End one member's harness: its container lives with the pod, its Skuld is asked."""
        await self._harness.stop(member, await self.broker_target(member))

    async def restart_hosted_member(self, member: Session) -> None:
        """Restart one member's harness under its new launch, and record that launch.

        The member's Secret is rewritten with the credential of the new launch (building the
        launch does it), then its Skuld replaces its process once the volume carries it.
        A container of the pod cannot be restarted through the API.
        """
        launch = await self.hosted_member_launch(member)
        launch_id = launch_id_of(launch.session.workload_config) or ""
        await self._harness.restart(member, await self.broker_target(member), launch_id)
        await self._record_member_launch(member, launch_id)

    async def _record_member_launch(self, member: Session, launch_id: str) -> None:
        """Note on the Deployment that the member's container now runs under ``launch_id``."""
        await self._ensure_client()
        name = self._release_name(member)
        deployment = await self._read_deployment(name)
        if deployment is None:
            raise HostedUnitError(f"The unit's Deployment {name} is gone; member {member.id} ended")
        entries = json.loads(
            ((deployment.metadata.annotations or {}).get(MEMBERS_ANNOTATION)) or "[]"
        )
        for entry in entries:
            if entry["id"] == str(member.id):
                entry["launch"] = launch_id
        await self._get_api("AppsV1Api").patch_namespaced_deployment(
            name=name,
            namespace=self._namespace,
            body={"metadata": {"annotations": {MEMBERS_ANNOTATION: json.dumps(entries)}}},
        )
        self._member_failures.pop(str(member.id), None)

    async def _unit_pod(self, release_name: str) -> Any:
        """The unit's pod, newest first; None while the Deployment has not made one."""
        api = self._get_api("CoreV1Api")
        pods = await api.list_namespaced_pod(
            namespace=self._namespace,
            label_selector=f"app.kubernetes.io/instance={release_name}",
        )
        live = [p for p in pods.items or [] if not p.metadata.deletion_timestamp]
        return live[0] if live else None

    @staticmethod
    def _recorded_launches(deployment: Any) -> dict[str, str]:
        """The launch each member's container was started with, as the Deployment recorded it."""
        annotations = (deployment.metadata.annotations or {}) if deployment.metadata else {}
        recorded = json.loads(annotations.get(MEMBERS_ANNOTATION) or "[]")
        return {str(entry["id"]): str(entry.get("launch") or "") for entry in recorded}

    async def _member_status(self, member: Session, deployment: Any) -> SessionStatus:
        """A member's status from its container in the unit's pod.

        A member started again by itself gets a new launch and credential, which a running
        container cannot be given: a pod's containers are restarted with the pod, never one
        at a time through the API. Such a member is failed, with the reason, rather than
        left running under a credential Forge has revoked.
        """
        launches = self._recorded_launches(deployment)
        member_id = str(member.id)
        if member_id not in launches:
            return SessionStatus.STOPPED
        launch = launch_id_of(member.workload_config)
        if launches[member_id] and launch and launches[member_id] != launch:
            self._member_failures[member_id] = (
                "A pod's containers cannot be restarted one at a time through the Kubernetes "
                "API, so this member cannot run under its new launch; stop the run and start "
                "it again"
            )
            return SessionStatus.FAILED
        self._member_failures.pop(member_id, None)
        pod = await self._unit_pod(self._release_name(member))
        if pod is None:
            return self._map_deployment_status(deployment)
        return _container_status(pod, self._member_container_name(int(member.hosted_port)))

    async def status_detail(self, session: Session) -> str | None:
        """Why a member is failed or not yet running, from the cluster's own record."""
        if session.hosted_by is None:
            return None
        if failure := self._member_failures.get(str(session.id)):
            return failure
        await self._ensure_client()
        pod = await self._unit_pod(self._release_name(session))
        if pod is None or session.hosted_port is None:
            return None
        state = _container_state(pod, self._member_container_name(int(session.hosted_port)))
        if state is None or state.running is not None:
            return None
        detail = state.waiting or state.terminated
        reason = getattr(detail, "reason", None) or "NotRunning"
        message = getattr(detail, "message", None)
        return f"container {self._member_container_name(int(session.hosted_port))}: {reason}" + (
            f": {message}" if message else ""
        )

    def _get_api_exception(self) -> type:
        """Get the ApiException class from kubernetes_asyncio."""
        from kubernetes_asyncio.client import rest

        return rest.ApiException

    def _get_api(self, api_class: str) -> Any:
        """Get a kubernetes API instance by class name."""
        from kubernetes_asyncio.client import (
            AppsV1Api,
            CoreV1Api,
            CustomObjectsApi,
            NetworkingV1Api,
            RbacAuthorizationV1Api,
        )

        api_map: dict[str, type] = {
            "RbacAuthorizationV1Api": RbacAuthorizationV1Api,
            "AppsV1Api": AppsV1Api,
            "CoreV1Api": CoreV1Api,
            "NetworkingV1Api": NetworkingV1Api,
            "CustomObjectsApi": CustomObjectsApi,
        }
        cls = api_map[api_class]
        return cls(self._api_client)

    async def _apply_resource(
        self,
        api_method_create: str,
        api_method_patch: str,
        api_class: str,
        name: str,
        manifest: dict[str, Any],
    ) -> None:
        """Create or patch a Kubernetes resource via the async API."""
        api = self._get_api(api_class)
        api_exception = self._get_api_exception()

        create_fn = getattr(api, api_method_create)
        patch_fn = getattr(api, api_method_patch)

        try:
            await create_fn(namespace=self._namespace, body=manifest)
        except api_exception as exc:
            if exc.status == 409:
                logger.info(
                    "%s %s already exists, patching",
                    api_class,
                    name,
                )
                await patch_fn(
                    name=name,
                    namespace=self._namespace,
                    body=manifest,
                )
            else:
                raise

    async def _apply_custom_resource(
        self,
        manifest: dict[str, Any],
    ) -> None:
        """Apply a custom resource (create or update)."""
        api = self._get_api("CustomObjectsApi")
        api_exception = self._get_api_exception()

        api_version = manifest["apiVersion"]
        parts = api_version.split("/")
        group = parts[0]
        version = parts[1]
        kind = manifest["kind"]
        plural = kind.lower() + "s"
        name = manifest["metadata"]["name"]
        namespace = manifest["metadata"].get(
            "namespace",
            self._namespace,
        )

        try:
            await api.create_namespaced_custom_object(
                group=group,
                version=version,
                namespace=namespace,
                plural=plural,
                body=manifest,
            )
        except api_exception as exc:
            if exc.status == 409:
                await api.patch_namespaced_custom_object(
                    group=group,
                    version=version,
                    namespace=namespace,
                    plural=plural,
                    name=name,
                    body=manifest,
                )
            else:
                logger.warning(
                    "Failed to apply %s %s: %s",
                    kind,
                    name,
                    exc,
                )
                raise

    async def _delete_custom_resource(
        self,
        group: str,
        version: str,
        plural: str,
        name: str,
    ) -> None:
        """Delete a custom resource, ignoring not-found errors."""
        api = self._get_api("CustomObjectsApi")
        api_exception = self._get_api_exception()

        try:
            await api.delete_namespaced_custom_object(
                group=group,
                version=version,
                namespace=self._namespace,
                plural=plural,
                name=name,
            )
        except api_exception as exc:
            if exc.status != 404:
                logger.warning(
                    "Failed to delete %s/%s: %s",
                    plural,
                    name,
                    exc,
                )

    async def _delete_resource(
        self,
        api_class: str,
        api_method: str,
        name: str,
    ) -> bool:
        """Delete a Kubernetes resource, returning True if deleted."""
        api = self._get_api(api_class)
        api_exception = self._get_api_exception()

        try:
            fn = getattr(api, api_method)
            await fn(name=name, namespace=self._namespace)
            return True
        except api_exception as exc:
            if exc.status != 404:
                logger.error(
                    "Failed to delete %s %s: %s",
                    api_class,
                    name,
                    exc,
                )
            return False

    async def _read_deployment(self, name: str) -> Any:
        """Read a Deployment, returning None if not found."""
        api = self._get_api("AppsV1Api")
        api_exception = self._get_api_exception()

        try:
            return await api.read_namespaced_deployment(
                name=name,
                namespace=self._namespace,
            )
        except api_exception as exc:
            if exc.status == 404:
                return None
            raise

    async def start(
        self,
        session: Session,
        spec: SessionSpec,
    ) -> PodStartResult:
        """Start Deployment + Service + Ingress for the session.

        A unit root's members (the member source's launches, one each) become containers of the
        same pod. Everything is checked and rendered before the cluster is touched, so a
        unit that cannot start whole starts nothing.
        """
        members = await self.hosted_member_launches(session)
        self._require_members(session, members)
        spec = self._with_brokered_credentials(spec)
        deployment = self._build_deployment_manifest(session, spec, members)
        await self._ensure_client()

        release_name = self._release_name(session)

        # Create the Traefik strip-prefix middleware CR.
        middleware = self._build_strip_prefix_middleware(session)
        await self._apply_custom_resource(middleware)

        # Create the nginx ConfigMap (must exist before the Deployment).
        nginx_cm = self._build_nginx_configmap(session, members)
        await self._apply_resource(
            api_method_create="create_namespaced_config_map",
            api_method_patch="patch_namespaced_config_map",
            api_class="CoreV1Api",
            name=f"{release_name}-nginx",
            manifest=nginx_cm,
        )

        # The pod lists pods to find its peers: its Role must exist before it starts. A
        # unit's Skulds find each other in a file of their own pod and list nothing.
        if not members:
            await self._apply_mesh_rbac(session, spec)

        # Create the Deployment.
        await self._apply_resource(
            api_method_create="create_namespaced_deployment",
            api_method_patch="patch_namespaced_deployment",
            api_class="AppsV1Api",
            name=release_name,
            manifest=deployment,
        )

        # Create the Service.
        service = self._build_service_manifest(session)
        await self._apply_resource(
            api_method_create="create_namespaced_service",
            api_method_patch="patch_namespaced_service",
            api_class="CoreV1Api",
            name=release_name,
            manifest=service,
        )

        # Create the Ingress.
        ingress = self._build_ingress_manifest(session)
        await self._apply_resource(
            api_method_create="create_namespaced_ingress",
            api_method_patch="patch_namespaced_ingress",
            api_class="NetworkingV1Api",
            name=release_name,
            manifest=ingress,
        )

        logger.info(
            "Created K8s resources for session %s in namespace %s",
            session.id,
            self._namespace,
        )

        return PodStartResult(
            chat_endpoint=self._chat_endpoint(session),
            code_endpoint=self._code_endpoint(session),
            pod_name=release_name,
        )

    async def stop(self, session: Session) -> bool:
        """Delete Deployment, Service, Ingress, and Middleware.

        A member of a unit has no Deployment of its own: it is a container of its unit's pod,
        which goes with the unit (stopping the root). Deleting the unit's Deployment for one
        member would end every other member too, so a member's stop removes nothing and says
        so; Forge ends the member's own session record itself.
        """
        if session.hosted_by is not None:
            return False
        await self._ensure_client()

        release_name = self._release_name(session)

        d1 = await self._delete_resource(
            "AppsV1Api",
            "delete_namespaced_deployment",
            release_name,
        )
        d2 = await self._delete_resource(
            "CoreV1Api",
            "delete_namespaced_service",
            release_name,
        )
        d3 = await self._delete_resource(
            "NetworkingV1Api",
            "delete_namespaced_ingress",
            release_name,
        )

        d4 = await self._delete_resource(
            "CoreV1Api",
            "delete_namespaced_config_map",
            f"{release_name}-nginx",
        )

        await self._delete_custom_resource(
            group="traefik.io",
            version="v1alpha1",
            plural="middlewares",
            name=f"{release_name}-strip",
        )

        d5 = d6 = False
        if mesh_lists_pods_of(self._mesh, self._namespace):
            mesh_rbac = self._mesh_rbac_name(session)
            d5 = await self._delete_resource(
                "RbacAuthorizationV1Api", "delete_namespaced_role_binding", mesh_rbac
            )
            d6 = await self._delete_resource(
                "RbacAuthorizationV1Api", "delete_namespaced_role", mesh_rbac
            )

        if d1 or d2 or d3 or d4 or d5 or d6:
            logger.info(
                "Deleted K8s resources for session %s",
                session.id,
            )

        return True

    async def status(self, session: Session) -> SessionStatus:
        """Get the current status of the session's pods."""
        await self._ensure_client()

        release_name = self._release_name(session)
        deployment = await self._read_deployment(release_name)

        if deployment is None:
            return SessionStatus.STOPPED

        if session.hosted_by is not None:
            return await self._member_status(session, deployment)
        return self._map_deployment_status(deployment)

    @staticmethod
    def _map_deployment_status(deployment: Any) -> SessionStatus:
        """Map Deployment status to SessionStatus."""
        status = deployment.status
        if status is None:
            return SessionStatus.STARTING

        ready = status.ready_replicas or 0
        desired = deployment.spec.replicas or 1

        if ready >= desired:
            return SessionStatus.RUNNING

        # Check for failure conditions.
        if status.conditions:
            for condition in status.conditions:
                if condition.type == "Available" and condition.status == "False":
                    if condition.reason in ("MinimumReplicasUnavailable",):
                        return SessionStatus.STARTING
                if condition.type == "Progressing" and condition.status == "False":
                    return SessionStatus.FAILED

        return SessionStatus.STARTING

    async def wait_for_ready(
        self,
        session: Session,
        timeout: float,
    ) -> SessionStatus:
        """Poll status until RUNNING or FAILED/timeout."""
        elapsed = 0.0
        while elapsed < timeout:
            current = await self.status(session)
            if current == SessionStatus.RUNNING:
                return SessionStatus.RUNNING
            if current == SessionStatus.FAILED:
                return SessionStatus.FAILED
            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval

        logger.warning("Timed out waiting for session %s after %.1fs", session.id, timeout)
        return await self.status(session)

    async def close(self) -> None:
        """Close the Kubernetes API client."""
        if self._api_client is not None:
            await self._api_client.close()
            self._api_client = None


def _container_state(pod: Any, container: str) -> Any:
    """The current state of ``container`` in ``pod``; None before the kubelet reports it."""
    for status in pod.status.container_statuses or []:
        if status.name == container:
            return status.state
    return None


def _container_status(pod: Any, container: str) -> SessionStatus:
    """A member's session status from its container's state."""
    state = _container_state(pod, container)
    if state is None:
        return SessionStatus.STARTING
    if state.running is not None:
        return SessionStatus.RUNNING
    if state.terminated is not None:
        return SessionStatus.STOPPED if state.terminated.exit_code == 0 else SessionStatus.FAILED
    waiting_reason = getattr(state.waiting, "reason", "") or ""
    if waiting_reason in _FAILED_WAITING_REASONS:
        return SessionStatus.FAILED
    return SessionStatus.STARTING
