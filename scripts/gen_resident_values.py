"""Generate gitops skuld-chart values for a resident ravn using the REAL
platform composition code (ResidentContributor + GatewayContributor +
FluxPodManager merge/translation) so the declared pod is identical to what
the platform would provision — minus the session row."""
import asyncio, copy, json, sys
import yaml

from volundr.adapters.outbound.contributors.resident import ResidentContributor
from volundr.adapters.outbound.contributors.gateway import GatewayContributor
from volundr.adapters.outbound.k8s_gateway import K8sGatewayAdapter
from volundr.adapters.outbound.flux import _deep_merge, _inject_workload_exchange_env
from volundr.domain.models import GitSource, Session
from volundr.domain.ports import SessionContext

OWNER = "76475334-b685-4299-b91d-1ec37f57e10f"
STABLE_ID = "muninn"
IMAGE_TAG = "dev-12b43b181762a72c94b8778a3163653e4d44d900"

session = Session(name="muninn", model="", source=GitSource(), owner_id=OWNER, tenant_id="default")
ctx = SessionContext(
    workload_type="resident",
    workload_config={
        "persona": "product-steward",
        "resident_name": "Muninn",
        "mimir": {"enabled": True, "instances": [
            {"name": "shared", "role": "shared",
             "url": "http://niuu-mimir-shared.volundr.svc.cluster.local"}]},
        "llm_config": {
            "model": "Qwen/Qwen3.6-35B-A3B-FP8",
            "provider": {"adapter": "ravn.adapters.llm.openai.OpenAICompatibleAdapter",
                         "kwargs": {"base_url": "https://qwen36-coder-llmd.valaskjalf.asgard.niuu.world",
                                    "api_key": ""}}},
        "daily_budget_usd": 100.0,
        "platform": {"enabled": True,
                     "base_url": "http://niuu-volundr.volundr.svc.cluster.local:80"},
    },
)

# Gateway adapter configured exactly as valhalla's values-niuu.yaml gateway kwargs
gateway = K8sGatewayAdapter(
    gateway_name="volundr-gateway", gateway_namespace="volundr",
    gateway_domain="sessions.valhalla.asgard.niuu.world",
    issuer_url="https://keycloak.niuu.world/realms/volundr", audience="volundr-api",
    jwks_uri="https://keycloak.niuu.world/realms/volundr/protocol/openid-connect/certs",
    workload_issuer_url="https://yggdrasil.niuu.world/api/v1/tokens/workload",
    workload_audience="volundr-api",
    workload_jwks_uri="https://yggdrasil.niuu.world/api/v1/tokens/workload/jwks",
    cors_origins=["https://volundr.valhalla.asgard.niuu.world",
                  "https://niuu.yggdrasil.niuu.world", "https://yggdrasil.niuu.world"],
)

resident = asyncio.run(ResidentContributor().contribute(session, ctx))
gw = asyncio.run(GatewayContributor(gateway=gateway).contribute(session, ctx))

# --- assemble like FluxPodManager.start ---
values: dict = {}
# lean session defaults (subset of podManager session_defaults that a room-mode
# resident needs; no repo checkout, no claude CLI creds, no code-server ingress)
_deep_merge(values, {
    "global": {"niuu": {"cluster": "valhalla"}},
    "image": {"repository": "ghcr.io/niuulabs/skuld", "tag": IMAGE_TAG, "pullPolicy": "IfNotPresent"},
    "volundr": {"apiUrl": "https://volundr.valhalla.asgard.niuu.world"},
    "ingress": {"enabled": False},
    "persistence": {"enabled": False},
    "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "fsGroup": 1000},
    "envSecrets": [],
})
_deep_merge(values, {"session": {"id": STABLE_ID, "name": "muninn", "model": ""}})
_deep_merge(values, gw.values)
_deep_merge(values, resident.values)

# pod_spec translation — mirror flux.py exactly
spec_pod = resident.pod_spec
env_vars = [dict(e) for e in spec_pod.env]
values["envVars"] = env_vars
if spec_pod.volumes:
    values["extraVolumes"] = [dict(v) for v in spec_pod.volumes]
if spec_pod.volume_mounts:
    values["extraVolumeMounts"] = [dict(v) for v in spec_pod.volume_mounts]
if spec_pod.init_containers:
    values["extraInitContainers"] = [dict(c) for c in spec_pod.init_containers]
if spec_pod.extra_containers:
    values["extraContainers"] = [dict(c) for c in spec_pod.extra_containers]
_inject_workload_exchange_env(values)

# ws-auth ownership: the broker enforces "only my ravns" from these
values["envVars"] += [
    {"name": "SKULD__SESSION__OWNER_ID", "value": OWNER},
    {"name": "SKULD__SESSION__TENANT_ID", "value": "default"},
]

# discovery metadata (ravn API resident discovery) + chat endpoint annotation
values["podLabels"] = {"niuu.world/kind": "resident", "niuu.world/persona": "product-steward"}
values["podAnnotations"] = {
    "niuu.world/resident-name": "Muninn",
    "niuu.world/chat-endpoint":
        "wss://sessions.valhalla.asgard.niuu.world/s/muninn/session",
}

# stabilize the session-derived broker peer id
raw = json.dumps(values)
sid8 = str(session.id)[:8]
raw = raw.replace(f"skuld-{sid8}", "skuld-muninn")
values = json.loads(raw)

header = """# Resident ravn "Muninn" — gitops-declared, NOT a Volundr session.
#
# This deploys the SKULD chart in room mode: broker + the product-steward
# ravn daemon — the exact pod shape the platform's ResidentContributor
# builds (this file is GENERATED with that same code; see the niuu repo,
# scratch: gen_muninn_values.py). Chat is the standard Skuld /session
# WebSocket behind the volundr-gateway with JWT validation; ownership is
# enforced by the broker (SKULD__SESSION__OWNER_ID). The ravn API discovers
# it via the niuu.world/kind=resident pod label.
#
# Volundr sessions only exist when Muninn LAUNCHES Ting workflow runs.
"""
out = header + yaml.safe_dump(values, sort_keys=False, width=100)
open(sys.argv[1], "w").write(out)
print(f"wrote {sys.argv[1]} ({len(out)} bytes); env count: {len(values['envVars'])}")
