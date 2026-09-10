# Bifrost chart: embedded Switchyard

Switchyard runs inside Bifrost, using the compiled extension in the Niuu image.
There is no Switchyard Deployment, sidecar, Rust installation, or runtime wheel
download. Use an image built from MR #954 or a subsequent release containing it;
an older image cannot enable this adapter. Select the published immutable image
tag explicitly rather than assuming the chart's default appVersion includes it.

## Infrastructure-repo values

For a **standalone Bifrost release**, merge
[values-switchyard-nemotron.yaml](values-switchyard-nemotron.yaml) into the
release's values and set `image.tag` to that published tag. This overlay routes
requests for `model: local` through native Switchyard to the real Valaskjalf
Nemotron endpoint. It deliberately leaves authentication, secrets, resources,
and ingress to the environment's existing configuration. The chart's default
authentication is open: retain your cluster's authentication settings.

For a **Niuu umbrella release**, nest the entire overlay under `bifrost`, alongside
`bifrost.enabled: true` and `bifrost.image.tag`. In a Flux HelmRelease, these go
under `spec.values`; the standalone variant uses `spec.values.config.selection`,
and the umbrella variant uses `spec.values.bifrost.config.selection`. Other GitOps
controllers should pass the same Helm values. No infrastructure repository or
cluster is modified by this chart change.

The chart renders `config` verbatim into the ConfigMap's `bifrost.yaml`, mounts
it at `/etc/bifrost/bifrost.yaml`, and passes that path to `python -m bifrost`.
Changing selection settings changes the pod's `checksum/config`, causing a
rollout. No additional enable flag is necessary: `config.selection.adapter`
selects the implementation. Omit `seed` in clusters so replicas seed independently.

Before reconciliation, render with the actual image tag in `NIUU_IMAGE_TAG`:

```sh
helm lint charts/bifrost -f charts/bifrost/values-switchyard-nemotron.yaml
helm template bifrost charts/bifrost \
  -f charts/bifrost/values-switchyard-nemotron.yaml \
  --set-string image.tag="$NIUU_IMAGE_TAG"
```

Layer your environment values as well when checking the complete deployment.
Verify the image reference, rendered selection, and provider settings. Keep
credentials out of `config` (it is a ConfigMap): the existing top-level
`providers` secret references, ESO, and key-vault configuration remain applicable.
The pod needs DNS/TLS/network access to Valaskjalf. Do not append `/v1` to this
provider's base URL; Bifrost's OpenAI adapter adds it.

## Weights and classifier routing

The overlay intentionally enables only Nemotron. One available target proves
native routing, not model comparison. When additional models are actually served,
declare their real model IDs in the provider and add named `selection.targets`
with `provider`, `model`, and `weight`. Targets can share a vLLM endpoint, but the
endpoint must actually serve each listed model. A route without `judge` selects
among its targets using relative weights (7 and 3 mean approximately 70/30).

For classifier selection, set the route's `judge` to a declared target and add
`prompt` and optional `max_tokens` (default 512). At least two answer target names
are required. The prompt must request JSON with `target` equal to an allowed
target name. Weights do not apply to classifier routes. Judge and answer calls
use the existing providers, authorization, usage records, and quota enforcement;
the judge adds latency and token spend. Invalid classifications fail the request,
not silently fall back. See the [operator guide](../../docs/operator/bifrost-switchyard-embedded.md)
for full weighted and classifier configurations.

Clients must request the configured route name (`local` here), not just the raw
Nemotron model ID. Routes are not automatically advertised in the managed model
catalog. Existing requests outside these routes retain normal Bifrost routing.

## Verify after the infrastructure rollout

Through the existing authenticated Bifrost endpoint, send an OpenAI-compatible
`POST /v1/chat/completions` (or the gateway-prefixed `/api/v1/bifrost/v1/chat/completions`)
with this body, then repeat with `stream: true`:

```json
{"model":"local","messages":[{"role":"user","content":"Reply with exactly SWITCHYARD_OK"}],"max_tokens":64,"temperature":0}
```

Check the returned actual model, Bifrost's Switchyard routing log with route,
target, provider and native outcome ID, and provider/model usage records. These
distinguish native routing from a direct model call. Classifier routes additionally
record a judge request ID containing `:classifier:`. The native build's source
revision and wheel hash are in `/opt/venv/share/niuu/switchyard-build.json`.

To disable selection, remove the overlay and set `config.selection: null` in the
effective values (`bifrost.config.selection` for the umbrella). Restore any client
route aliases or model settings needed for direct routing: `local` is no longer a
Switchyard route once the adapter is disabled. Reconcile via the infrastructure
repository; no cluster-side package uninstall is needed.
