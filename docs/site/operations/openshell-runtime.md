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
`OpenShellVmRuntime` through the [VM compute lifecycle](../../operator/vm-compute.md#openshell-on-managed-vms).
For an independently managed plain VM, install Docker and run the OpenShell gateway with
`compute_driver = "docker"` under `[openshell.gateway]` in its version 2 TOML
configuration. Configure `[openshell.drivers.docker]` with the sandbox image,
matching supervisor image, Docker network and gateway callback endpoint. Connect
Niuu through the same authenticated gateway adapter. Use the gateway version's
configuration preflight before starting its service.

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
| Labels and annotations | Sandbox metadata |
| Literal environment values | Sandbox environment |
| Resource requests and limits | Sandbox template resources |
| Node selector and tolerations | Kubernetes driver scheduling fields |
| Runtime class and priority class | Kubernetes driver pod fields |

Ravn flocks use regular peer containers in the Niuu OpenShell fork. Each persona
has its own image, process supervisor, and inline non-secret configuration. The
containers share pod localhost, the Forge workspace/home PVC mounts, and an
`emptyDir` at `/tmp/niuu-mesh` for Unix sockets. Local Mimir volumes are shared too;
the sandbox policy must allow `/mimir/local` when that capability is configured.

Deploy the matching fork gateway, static supervisor, and Helm chart, with
`supervisor.topology=sidecar`. This configures OpenShell's enforcement component;
Ravn workloads are regular Kubernetes `spec.containers` entries. Set
`volundr.ravnFlockImage` to a pinned image containing Niuu and Python. For service
configuration the setting is `ravn_flock_image`.

The contributor emits `openshell.workloads`, `openshell.volumes`, and
`openshell.volumeMounts`. Völundr maps these to the Kubernetes driver configuration
and gives each workload the shared persistent mounts. Workloads wait for a unique
startup marker while the primary completes repository checkout and credential-file
projection, then start their daemons. Do not attach Kubernetes readiness probes to
this startup gate: Völundr needs the ready primary exec channel to release it.
Startup fails after five minutes if workspace bootstrap never completes.

Gateway credentials remain with OpenShell's network supervisor. Dynamic provider
updates reach every workload. Direct credential environment materialization is
rejected for peer workloads; use the existing dynamic provider grants. Persona
sources must be in the image or HTTP-backed; arbitrary ConfigMap/init-container
passthrough is unsupported. The primary remains the public exec/SSH target. Peer
stdout is also written to `/sandbox/workspace/.flock/logs/<persona>.log`.

The fork's `Niuu Dev Images` workflow publishes both architectures only after
its Kubernetes localhost and egress acceptance test succeeds. Images use full
commit tags and the matching chart uses `0.0.0-niuu.sha<commit>`. Niuu's existing
`dev` workflow builds the application/runtime images. Deploy these through
cluster-specific GitOps values.

The adapter uses the upstream SDK for commit `5b9daab93`, pinned by wheel hash
in `pyproject.toml` and `uv.lock`. The unchanged CI-built wheel is mirrored in
the fork's `niuu-7bd0ed45e` release so upstream's rolling dev release cannot
remove the dependency. This gateway API requires an explicit workspace selector
and page tokens; the adapter selects the existing `default` workspace for all
workspace-scoped calls. Provider profiles are always active in this gateway; the removed
`providers_v2_enabled` setting must not be sent. The released `0.0.116` SDK is not compatible with this
gateway revision.

Ravn peers read `SKULD__VOLUNDR_API_URL` through their typed runtime configuration
to reach the same Codex credential broker as Skuld. OpenShell's provider proxy
authenticates that service call; peers do not need a projected service-account
token. A configured Codex broker without a platform URL fails explicitly.

## Validate a target

Create a session through the normal Forge launch flow on the intended target.
Verify the sandbox becomes ready, Skuld is exposed, chat streams an actual model
answer, and reconnect restores the expected history. Inspect logs under the
correct session and owner.

For a flock, verify Skuld and at least two Ravn personas are regular containers
in the same pod and exchange mesh messages bidirectionally over localhost. Check
shared workspace files and Unix sockets, authenticated provider/service calls,
and denial of direct outbound connections. A required peer exiting must fail the
group closed; stop/delete must clean up the whole sandbox. The shared localhost
network may be private, but must retain OpenShell's network and process controls.

Test each control advertised by a resident profile. A gateway metrics endpoint
is not evidence that a resident-scoped metrics control exists. Test restart and
persistence according to the selected profile's contract.

Stopping an OpenShell session removes its exposure, sandbox, and owned provider
resources; it does not have the same retention behavior as a local workspace.
Preserve required output before stopping. Verify cleanup after a normal stop and
a failed launch. These checks need a real gateway and are not covered by the local
bootstrap test.
