# Observatory Valkyrie Architecture

## Goal

Make Observatory render a real, discovered platform topology instead of a thin
projection of registered instances, while keeping the design portable for
non-Niuu deployments.

The target split is:

- `Guild` owns registry, visibility, fan-out, merge, and operator-facing APIs.
- `Valkyrie` runs per cluster and discovers cluster-local infrastructure with
  minimal RBAC.
- `Ting`, `Volundr`, `Ravn`, `Mimir`, `Bifrost`, and future services expose
  service-owned observatory fragments and optional layout hints.
- `Observatory` renders the merged snapshot and applies a hybrid layout.

## Why Valkyrie

Guild should not need direct Kubernetes access to every cluster.

A per-cluster Valkyrie gives us:

- one trusted scout per cluster
- cluster-local RBAC instead of central broad credentials
- a stable portability seam for Kubernetes, Nomad, bare metal, or static files
- a place to enrich raw infra facts before they hit Guild

## Discovery Layers

### Layer 1: Registry

Guild already knows which instances exist and which principals can see them.
That stays the first filter.

### Layer 2: Cluster scout

Each Valkyrie discovers cluster-local facts such as:

- namespaces
- nodes
- deployments
- statefulsets
- pods
- services
- ingresses
- EndpointSlices
- selected custom resources later

Valkyrie projects those into observatory nodes, edges, health, and layout hints.

### Layer 3: Service-owned fragments

Services provide semantic internals that infra discovery cannot infer well:

- Ting: runs, workflow state, queue depth, active dispatchers
- Volundr: sessions, workspaces, capacity
- Ravn: wardens, long-lived ravens, ephemeral ravens
- Mimir: mounts, page counts, write pressure
- Bifrost: providers, model edges, request rates

This keeps each service authoritative for its own meaning.

## Portability Model

Observatory discovery should follow the same adapter pattern already used by
Ravn discovery.

Recommended adapter set:

- `registry`: Guild-registered instances
- `valkyrie`: cluster-local scout endpoint
- `k8s`: direct fallback where Valkyrie is not deployed
- `static`: YAML or JSON topology file
- `consul`: service catalog + health
- `composite`: merge multiple adapters

This lets others build their own Observatory surface without needing the full
Niuu stack.

## Contracts

### 1. Guild aggregate snapshot

`GET /api/v1/niuu/observatory/snapshot`

Returns the merged, principal-filtered topology that the frontend consumes.

### 2. Valkyrie fragment endpoint

`GET /api/v1/valkyrie/observatory/fragment`

Returns cluster-local infrastructure facts and layout hints.

Primary responsibility:

- identify realms, clusters, workloads, services, endpoints, and health
- annotate nodes with labels, namespaces, and ownership
- publish cluster-level layout hints

### 3. Service fragment endpoint

`GET /api/v1/<service>/observatory/fragment`

Returns service-owned semantic topology.

Primary responsibility:

- emit semantic nodes and edges
- map them to realm / cluster / host / parent containers
- add local layout hints where the service has better knowledge

### 4. Optional layout endpoint

`GET /api/v1/<service>/observatory/layout`

This is optional. In the simple case, layout hints can live inside the fragment
payload. A separate endpoint only makes sense if layout computation is
expensive, operator-authored, or versioned independently from topology.

## Shared Fragment Shape

Every fragment should be able to return:

- `nodes`
- `edges`
- `events`
- `layoutHints`
- `meta`

Important rules:

- Node ids must be globally stable.
- Fragments may emit partial graphs.
- Guild is responsible for merge, dedupe, and conflict resolution.
- Services should prefer semantic parents over raw visual coordinates.

## Layout Strategy

Discovery should answer what exists and how it is related.
Layout should answer where it is drawn.

Recommended hybrid layout:

- realm placement: pinned anchors or operator hints first
- realm fallback: global orbit / star-map distribution
- cluster placement inside realm: D3 pack
- local cluster internals: D3 pack plus light constrained force relaxation
- operator overrides: optional persisted layout hints

This keeps the visual quality of the current Observatory while making layout
data-driven.

## D3 Pack Usage

Use D3 pack for containment-heavy local groups, not for the entire world map.

Good fit:

- realms containing clusters
- clusters containing services, runs, and ravens
- minimap-friendly local density management

Not sufficient alone:

- cross-realm composition
- long cross-links
- curated “constellation” placement

So the intended mode is `hybrid`, not pure pack.

## Merge Rules In Guild

Guild should merge fragments in this order:

1. Registry-visible instances
2. Valkyrie infra fragments
3. Service semantic fragments
4. Operator layout overrides

Recommended conflict policy:

- identity and visibility: Guild
- infra status and placement scope: Valkyrie
- semantic children and metrics: service owner
- final pinned anchors: operator override

## Kubernetes RBAC For Valkyrie

Start read-only and narrow:

- `get`, `list`, `watch` on `pods`
- `get`, `list`, `watch` on `services`
- `get`, `list`, `watch` on `endpointslices`
- `get`, `list`, `watch` on `deployments`
- `get`, `list`, `watch` on `statefulsets`
- `get`, `list`, `watch` on `nodes`
- `get`, `list`, `watch` on `namespaces`
- optional `get`, `list`, `watch` on `ingresses`

CRDs can come later once there is a clear topology need.

## First Implementation Slice

1. Add shared observatory fragment and layout-hint contract types.
2. Add Guild-side merge support for `layoutHints`.
3. Add a Valkyrie observatory fragment endpoint contract.
4. Add one real service fragment endpoint, starting with Ting.
5. Add a D3-pack-based cluster sublayout behind a feature flag.

## Success Criteria

- Observatory is no longer limited to kind-grouped instance health.
- Realms and clusters can come from discovery rather than static seeds.
- Service-owned semantic nodes appear without Guild knowing service internals.
- D3 pack improves containment clarity without losing the current visual tone.
