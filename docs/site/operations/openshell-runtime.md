# Run sessions through OpenShell

Völundr's `OpenShellGatewayPodManager` creates and manages sandboxes through the
OpenShell gateway gRPC API. It then starts Skuld with `ExecSandbox` and exposes
its session service through `ExposeService`.

The gateway can use Kubernetes as its compute driver. An OpenShell sandbox
configuration is not an arbitrary multi-container Kubernetes pod specification.

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

Codex subscription support uses the configured OpenBao credential and token-refresh
path. Claude Code's built-in OpenShell provider profile supports API keys; local
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

Ravn flock contributions use a structured process plan with Skuld and the Ravn
processes. Do not assume unrelated extra containers, init containers, volume
mounts, or service accounts will be carried through the gateway. Inspect adapter
logs and supported input handling before using a Kubernetes-oriented launch spec.

## Validate a target

Create a session through the normal Forge launch flow on the intended target.
Verify the sandbox becomes ready, Skuld is exposed, chat streams an actual model
answer, and reconnect restores the expected history. Inspect logs under the
correct session and owner.

Test each control advertised by a resident profile. A gateway metrics endpoint
is not evidence that a resident-scoped metrics control exists. Test restart and
persistence according to the selected profile's contract.

Stopping an OpenShell session removes its exposure, sandbox, and owned provider
resources; it does not have the same retention behavior as a local workspace.
Preserve required output before stopping. Verify cleanup after a normal stop and
a failed launch. These checks need a real gateway and are not covered by the local
bootstrap test.
