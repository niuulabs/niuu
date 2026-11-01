# Run sessions through OpenShell

Völundr's `OpenShellGatewayPodManager` creates and manages sandboxes through the
OpenShell gateway gRPC API. It then starts Skuld with `ExecSandbox` and exposes
its session service through `ExposeService`.

The gateway selects its compute driver independently of the Niuu adapter.
OpenShell supports Docker on a plain Linux host as well as Kubernetes. Running
OpenShell on a VM does not require installing Kubernetes inside that VM. The
`OpenShellGatewayPodManager` name refers to Niuu's session lifecycle port; it does
not select the gateway's compute driver.

For managed VM provisioning, reuse and automatic cleanup, configure
`OpenShellVmRuntime` through the [VM compute lifecycle](https://github.com/niuulabs/niuu/blob/dev/docs/operator/vm-compute.md#openshell-on-managed-vms).
For an independently managed plain VM, install Docker and run the OpenShell gateway with
`compute_driver = "docker"` under `[openshell.gateway]` in its version 2 TOML
configuration. Configure `[openshell.drivers.docker]` with the sandbox runtime
image and supervisor image of one OpenShell build (`sandbox_runtime_image`,
`supervisor_image`) and the gateway callback endpoint (`grpc_endpoint`). Since
v0.1.6 sandboxes do not join a Docker network: the workload container runs with
`network=none` and its supervisor on the host network, and the removed
`network_name` key fails the gateway's preflight. Connect Niuu through the same
authenticated gateway adapter. Use the gateway version's configuration preflight
before starting its service.

For Kubernetes, an OpenShell sandbox configuration is not an arbitrary
multi-container Kubernetes pod specification.

## Prerequisites and configuration owner

You need a reachable OpenShell gateway, its configured authentication authority,
a compatible sandbox image, and a Forge target configured with the OpenShell
adapter. Provider v2 dynamic grants additionally require the sandbox workload
identity and credential-exchange path described below.

Configure these adapter kwargs in the Völundr service, or under
`volundr.podManager.kwargs` in umbrella Helm values:

| Field | Supply from your deployment |
| --- | --- |
| `gateway_endpoint` | Reachable gateway gRPC host and port |
| `compute_driver` | Driver configuration envelope: `kubernetes` by default; select `docker` for Docker mounts |
| `token_url` | OIDC client-credentials token endpoint |
| `client_id` | Gateway machine-client identifier |
| `sandbox_image` | Pinned image containing the required supervisor and session runtime |
| `sandbox_command` | Command in that image that starts the session runtime |
| `sandbox_workspace`, `sandbox_home` | Paths supported by that image |
| `service_port` | Port Skuld serves inside the sandbox |

Select adapter
`volundr.adapters.outbound.openshell_gateway.OpenShellGatewayPodManager`.
For service YAML, adapter arguments are under `pod_manager.kwargs`, and
`pod_manager.secret_kwargs_env` maps the client secret to an environment-variable
name. Helm's `podManager.secretKwargs` instead maps a kwarg to a Kubernetes secret
name and key. Do not put the secret value into a committed values file.

Use the actual adapter constructor and selected chart version as the contract;
internal cluster hostnames and old development image tags are not portable defaults.

## Three authentication boundaries

| Caller | Target | Identity |
| --- | --- | --- |
| Völundr | OpenShell gateway | Configured OIDC machine-client bearer token |
| Sandbox supervisor | OpenShell gateway | Service-account bootstrap and sandbox JWT |
| Sandbox provider proxy | Völundr credential endpoint | SPIFFE JWT-SVID for a scoped grant |

For SPIFFE-backed grants, configure `credential_token_endpoint`,
`spiffe_jwks_uri`, `spiffe_issuer`, `spiffe_audience`, and `spiffe_subject_prefix`
from your trust authority and internal exchange service. The sandbox needs its
Workload API socket. Verify issuer, audience, and subject against that deployment;
do not copy another cluster's trust domain.

## Provider grants and runtime login

The Provider v2 path exchanges sandbox identity for credentials from the configured
OpenBao backend. Völundr validates the sandbox/session/owner/provider relationship
before returning the requested grant. The provider profile controls the HTTP
endpoints where it may be used.

Codex subscription support uses an OpenBao `oauthapp` grant. OpenBao owns renewal;
the authenticated broker delivers access tokens and account metadata. See
[credential renewal](security-and-permissions.md#codex-subscription-credentials). Claude Code's built-in OpenShell provider profile supports API keys; local
Claude subscription OAuth state is not a supported dynamic provider grant. Host
login success therefore does not validate an OpenShell Claude session.

Provider grants do not mount arbitrary home-directory files. Put non-secret runtime
defaults in the image or supported session configuration.

## Inputs that map to a sandbox

| Session input | Mapping |
| --- | --- |
| Völundr's own labels and annotations | Sandbox metadata (`CreateSandboxRequest.labels`/`annotations`) |
| Pod-spec labels and annotations | Sandbox Pod (`SandboxTemplate.labels`/`annotations`), admitted by the gateway's allowlists |
| Literal environment values | Sandbox environment |
| Resource requests and limits | Sandbox template resources |
| Node selector and tolerations | Kubernetes driver scheduling fields |
| Runtime class and priority class | Kubernetes driver pod fields |

Ravn flocks run as peer workload containers, which the Niuu OpenShell fork
re-implements on OpenShell v0.1.6's sandbox runtime
(`driver_config.kubernetes.containers.workloads`). Each persona is one peer:
a regular container in the sandbox's workload Pod, from its own image, sharing
the Pod's localhost, the Forge workspace/home PVC mounts, and an `emptyDir` at
`/tmp/niuu-mesh` for Unix sockets. Local Mimir volumes are shared too; the
sandbox policy must allow `/mimir/local` when that capability is configured. The
driver makes each peer's entrypoint OpenShell's subordinate runtime
(`openshell-sandbox peer`), which applies the sandbox's Landlock, seccomp and
network mediation to the persona's command, so peers add no isolation boundary
of their own. Set `volundr.ravnFlockImage` (service configuration:
`ravn_flock_image`) to a pinned image containing Niuu and Python.

The contributor emits `openshell.workloads`, `openshell.volumes`, and
`openshell.volumeMounts`; Völundr maps them to the Kubernetes driver
configuration and checks them against the driver's rules before it creates the
sandbox (`volundr.adapters.outbound.openshell_contract`):

- A peer carries exactly `name`, `image`, `command`, `environment`, `resources`
  and `volume_mounts`. Anything else is rejected, including `readiness_port`,
  which earlier fork builds accepted.
- Names are DNS-1123 labels of at most 40 characters, unique, and not `agent`,
  `supervisor` or anything starting with `openshell`. A persona whose
  `ravn-<persona>` name breaks this fails the launch: rename the persona.
- At most 16 peers per sandbox. `command` is explicit (peers never fall back to
  the image entrypoint), environment keys never start with `OPENSHELL_` or
  contain `=`, and resource quantities are strings.
- Volumes are `empty_dir: {}` (no options) or `persistent_volume_claim`; mounts
  must reference a declared volume, and may not overlap `/.openshell` or `/run`.

Workloads wait for a unique startup marker while the primary completes
repository checkout and credential-file projection, then start their daemons.
Startup fails after five minutes if workspace bootstrap never completes. Peers
have no readiness probes: workload listeners bind `127.0.0.1`, which kubelet
cannot probe.

The workload Pod runs with `restartPolicy: Never`. When a peer exits, OpenShell
terminates the whole sandbox; when the primary exits, every peer is terminated.
Peers receive provider credentials as they were at start; later provider updates
do not reach them. Direct credential environment materialization is rejected for
peer workloads; use the existing dynamic provider grants. Persona sources must be
in the image or HTTP-backed; arbitrary ConfigMap/init-container passthrough is
unsupported. The primary remains the only exec/SSH target. Peer stdout is
written to `/sandbox/workspace/.flock/logs/<persona>.log`.

### Gateway requirements

Peers and every Forge storage mount travel in the sandbox's
`template.driver_config`, which OpenShell v0.1.6 refuses unless the operator
allows it. Set these in the gateway chart's `gatewayConfig` (or the gateway
TOML):

```toml
[openshell.drivers.kubernetes]
# Required: Forge PVC mounts, scheduling fields and flock peers are driver config.
allow_driver_config = true
# One fork build for all three images: the fork publishes
# ghcr.io/jve/openshell/{gateway,sandbox,supervisor}:<commit>.
sandbox_runtime_image = "ghcr.io/jve/openshell/sandbox:<commit>"
supervisor_image      = "ghcr.io/jve/openshell/supervisor:<commit>"
# Pod labels/annotations Völundr sets on sandbox Pods (see below). Keys under
# openshell.ai and agents.x-k8s.io can never be allowed.
allowed_pod_labels      = ["ravn.niuu.world/*"]
allowed_pod_annotations = ["ravn.niuu.world/*"]
```

With the fork's chart (`oci://ghcr.io/jve/openshell/helm-chart`, version
`0.0.0-niuu.sha<commit>`), the same settings are
`server.drivers.kubernetes.allowDriverConfig=true`, `sandboxRuntime.image.*` and
`supervisor.image.*`; enabling `allowDriverConfig` also grants the gateway the
PVC metadata reads resource admission needs (set `gateway.allowDriverConfig=true`
in any split workspace release too).

- **Resource admission.** Leave `[openshell.drivers.kubernetes.resource_admission]`
  enabled. Every PVC a sandbox mounts must carry
  `openshell.ai/sandbox-attachable: "true"` and
  `openshell.ai/sandbox-attachable-workspace: "default"`. Völundr labels the
  residents' own claims itself (`sandbox_attachable_labels` on the pod manager,
  defaulting to those two labels; set it to match a custom `required_labels`).
  For Forge session homes and workspaces, set the storage adapter's
  `pvc_labels` kwarg to the same labels; existing claims are labelled the next
  time the home is provisioned, and older session workspaces must be labelled
  once by hand:
  `kubectl label pvc -n <namespace> -l volundr/pvc-type=workspace openshell.ai/sandbox-attachable=true openshell.ai/sandbox-attachable-workspace=default`.
- **Pod labels and annotations.** Völundr keeps its own `volundr.niuu.io/*`
  bookkeeping on the sandbox's gateway metadata, not the Pod. Only what
  something reads from the Pod goes there: a flock resident's
  `ravn.niuu.world/*` labels and annotations (Ravn's Kubernetes discovery) and
  any pod-spec labels or annotations session contributors set. List those keys
  in `allowed_pod_labels` / `allowed_pod_annotations`, or the gateway rejects
  the sandbox.
- **No injected containers.** The fork's admission gate releases a sandbox Pod
  only if it contains exactly the rendered peers followed by `agent`; any other
  container or ephemeral container fails the sandbox closed. Do not enable
  mutating webhooks that inject containers into the sandbox namespace (service
  mesh sidecar injection such as `istio-injection=enabled` or
  `linkerd.io/inject`, Vault/OpenBao agent injection by namespace default, and
  similar). Völundr already drops `vault.hashicorp.com/*` annotations from
  OpenShell sandboxes.

The fork's `Niuu Dev Images` workflow publishes both architectures only after
its Kubernetes acceptance tests succeed. Images use full commit tags and the
matching chart uses `0.0.0-niuu.sha<commit>`; pin the gateway, sandbox runtime,
supervisor and chart to the same commit through cluster-specific GitOps values.
Niuu's own `dev` workflow builds the application/runtime images.

### SDK and protocol

The adapter uses the fork's SDK at commit `03d0f94d4` (OpenShell
v0.1.6-pre.5 plus the peer-workload driver), pinned by wheel hash in
`pyproject.toml` and `uv.lock` and published in the fork's `niuu-sdk-03d0f94d4`
release. Against this protocol the adapter:

- addresses sandboxes by canonical name in every exec, log and forward call, and
  selects the `default` workspace for all workspace-scoped calls;
- opens TCP forwards with the gateway principal alone (no SSH session token);
- sends exec timeouts and grant cache TTLs as `Duration`s;
- maps network endpoint modes to OpenShell's enums. `tls: terminate` and
  `tls: passthrough` are rejected with a remedy: omit `tls` to keep automatic
  TLS termination, or set `tls: skip` deliberately;
- treats `DeletionOutcome` ACCEPTED as "deletion pending" and keeps polling, and
  refuses an unspecified outcome.

`tests/test_adapters/test_openshell_protocol.py` builds every request the adapter
sends with the installed SDK.

### Upgrading a gateway to v0.1.6

- Stopped sessions whose sandbox was created by an older gateway cannot be
  resumed: OpenShell re-admits a sandbox's attachments on start and refuses
  state without admission provenance. Völundr reports this with the remedy;
  archive what the session needs and start a new one.
- Stored gateway state that contains `tls: terminate` (sandbox or provider
  policies written by older gateways or clients) fails validation when read.
  Start the upgraded gateway on a fresh database, or remove those values first.
- Label existing PVCs as described above before the first launch.

Ravn peers read `SKULD__VOLUNDR_API_URL` through their typed runtime configuration
to reach the same Codex credential broker as Skuld. OpenShell's provider proxy
authenticates that service call; peers do not need a projected service-account
token. A configured Codex broker without a platform URL fails explicitly.

## Validate a target

Create a session through the normal Forge launch flow on the intended target.
Verify the sandbox becomes ready, Skuld is exposed, chat streams an actual model
answer, and reconnect restores the expected history. Inspect logs under the
correct session and owner.

For a flock, verify Skuld and at least two Ravn personas run as peer containers
in the same workload Pod and exchange mesh messages bidirectionally over localhost. Check
shared workspace files and Unix sockets, authenticated provider/service calls,
and denial of direct outbound connections. A required peer exiting must fail the
group closed; stop/delete must clean up the whole sandbox. The shared localhost
network may be private, but must retain OpenShell's network and process controls.

Since v0.1.6 a Kubernetes sandbox is a workload Pod paired with a supervisor
Pod. After an upgrade, verify that provider grants still resolve: the SPIRE
`ClusterSPIFFEID` for sandbox supervisors must still issue
`spiffe_subject_prefix` + the sandbox ID (`spiffe://niuu.world/openshell/sandbox/<id>`
by default), which `exchange_credential_grant` looks up with `ListSandboxes`.
Verify also that Völundr's TCP forward to the sandbox's `127.0.0.1:<port>`
(long sandbox names, Hermes and Ravn-session residents) still reaches the
service.

Test each control advertised by a resident profile. A gateway metrics endpoint
is not evidence that a resident-scoped metrics control exists. Test restart and
persistence according to the selected profile's contract.

Stopping an OpenShell session removes its exposure, sandbox, and owned provider
resources; it does not have the same retention behavior as a local workspace.
Preserve required output before stopping. Verify cleanup after a normal stop and
a failed launch. These checks need a real gateway and are not covered by the local
bootstrap test.
