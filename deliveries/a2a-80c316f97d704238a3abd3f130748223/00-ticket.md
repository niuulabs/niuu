# NIU-1114 — A2A: validate resident tool build end-to-end in dev

- **Tracker:** [NIU-1114](https://linear.app/niuu/issue/NIU-1114/a2a-validate-resident-tool-build-end-to-end-in-dev)
- **Niuu source and target:** `regin` only
- **Operator gate:** `33413b2738ff`
- **Infrastructure review base:** `jve/infrastructure` `main`; Fleet is the only deployment mechanism.

## Acceptance criteria

A real dev resident must commission a capability-gap build through A2A, complete
verification and court review, install the resulting learned tool, and retain
identifiers/evidence for every phase. `ravn tool-build doctor` must be green and
the operator runbook must retain reproducible A2A configuration. Do not mark the
ticket complete on configuration or a code-only proof alone.

## Selected deployment target

The selected resident is **Runa**, `valkyrie-valhalla-k8s`, in the named
**Valhalla** Teleport cluster. The active infrastructure source has the complete
A2A wiring, a unique public callback, workload identity, a `valhalla` realm
binding, and its own read-only Kubernetes inspection capability. Its public HUD
reported zero queued tasks at 2026-10-25T13:56Z; Sigrun was not selected because
its queue was at its configured capacity (50/50).

## Exact GitOps configuration required

Inspection of `jve/infrastructure` at
`e211e1c427906dcb921bffdf02180a77e3ccaa9d` found the required configuration
already present. No speculative or duplicate infrastructure edit is warranted.

`valhalla/valkyries/manifests/configmap.yaml` configures:

```yaml
gateway:
  platform:
    enabled: true
    base_url: https://yggdrasil.niuu.world
    a2a_default_connection_id: Valhalla
    a2a_push_callback_url: https://runa.valhalla.asgard.niuu.world/a2a/push
    workload_token_file: /var/run/secrets/niuu-workload/token
    workload_exchange_url: https://yggdrasil.niuu.world/api/v1/tokens/workload/exchange
  channels:
    http:
      enabled: true
      a2a_push_enabled: true
      a2a_push_auth:
        name: yggdrasil-workload
        adapter: niuu.adapters.workload_identity.jwt.JwtWorkloadIdentityVerifier
        kwargs:
          issuer: https://yggdrasil.niuu.world/api/v1/tokens/workload
          audiences: [ting]
          jwks_uri: https://yggdrasil.niuu.world/api/v1/tokens/workload/jwks
      a2a_push_required_claims:
        workload_service: ting
resident_evolution:
  realm_slug: valhalla
  autonomy_mode: autonomous
  realm_api_base_url: https://yggdrasil.niuu.world
  tool_build_adapter: ravn.adapters.tool_build.a2a.A2AToolBuildBackend
  tool_build_kwargs:
    card_url: https://yggdrasil.niuu.world/.well-known/agent-card.json
    connection_id: Valhalla
    max_poll_attempts: 360
    workload_token_file: /var/run/secrets/niuu-workload/token
    workload_exchange_url: https://yggdrasil.niuu.world/api/v1/tokens/workload/exchange
    workload_audiences: [volundr-api, forge, ting, mimir, guild]
  tool_builder_workflow:
    tags: [tool-builder]
```

`valhalla/valkyries/manifests/service.yaml` exposes the HTTPS
`/a2a/push` route for `runa.valhalla.asgard.niuu.world`. The central Ting
configuration in `ymir/niuu-app/helm/values-niuu.yaml` allow-lists that origin
and supplies `A2A__PUSH_ENCRYPTION_KEY` through an existing Kubernetes secret;
no secret value belongs in Git.
