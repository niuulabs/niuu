# Equipment on the Observatory: from polling to stewardship

Status: **agreed direction, not scheduled.** Written down so the current
polling adapter is understood as a step rather than the destination.

## Where it stands

Eitri's print farm reaches the canvas through
`LaevateinnGatewayDiscoveryAdapter` — one adapter instance per Laevateinn
gateway, configured in the infrastructure repo at
`base/observatory/helm/fleet.yaml` under the `eitri` target customization.
Each instance reads `GET /api/printers` from one gateway and emits the devices
it fronts.

The adapter deliberately knows one thing: how to talk to a Laevateinn gateway.

- `kind` comes from configuration, so the registry stays the single vocabulary
  of what things are. The adapter refuses to start without it rather than
  guessing.
- Device fields are carried across as the gateway names them, so a field the
  gateway gains needs no release.
- Status is shallow — reachable, faulted, or working. Reading meaning into a
  device's own numbers is a steward's job, not a discovery adapter's.

## Why this is not the destination

Polling means the platform has to know about every equipment system: a new
class of machine behind a different controller needs a new adapter here, and
the Observatory ends up accumulating knowledge of hardware it has no
relationship with.

It also cannot answer the question that actually matters once agents run the
workshop. The canvas can say *four printers exist and one is faulted*. It
cannot say *Ivaldi owns these four, it noticed the fault, and here is what it
did about it* — because the agent that stewards the equipment is not the thing
reporting it.

## The direction

**The agent that stewards equipment publishes it.** A resident already knows
what it manages, far better than a poller does — that judgment is Ravn-owned
by the boundary rules, and inferring it in Niuu infrastructure would violate
them.

Every piece already exists:

| Piece | Where |
|---|---|
| Ravn produces a topology fragment | `src/ravn/domain/environment.py` — `to_observatory_fragment()` |
| Push inbox accepts fragments | `PUT /api/v1/niuu/observatory/fragments/{source_id}` (`src/niuu/adapters/inbound/rest_instances.py`) |
| Scoped credential for pushing | `observatory:topology:push` in `KNOWN_WORKLOAD_SCOPES` |
| Relation for ownership | `manages` in `_RELATION_TO_EDGE_KIND` |

So the work is for a steward to include its equipment in the fragment it
already produces, with a `manages` edge back to itself. Nothing about the
merge changes — a pushed fragment and a polled one land in the same place.

## What that buys

- Equipment that cannot be polled (a bench machine, anything NAT'd) arrives on
  the same path as everything else, which is what the push inbox exists for.
- The graph gains *stewardship*, not just inventory: who owns this machine,
  who noticed it break.
- The platform stops needing an adapter per equipment vendor.

## Migration

The two coexist without conflict — same contract, same merge. When Ivaldi
publishes eitri's farm, delete the four adapter blocks from `fleet.yaml`. The
adapter stays useful for equipment no agent stewards yet.

## The registry seed needs to stop being code

Adding an entity type today means one of two things, and neither is right.

- **`POST /api/v1/observatory/registry/types`** — no code, takes effect
  immediately, but the registry is per-Observatory, so it has to be repeated
  against every cluster and nothing records that it happened. It also drifts:
  a cluster installed later never gets it.
- **`src/observatory/data.py` plus a version bump** — reaches every cluster
  and is reviewable, but it is a code change and a release to add a *kind of
  thing*, which is exactly the sort of edit an operator should be able to make
  without touching a repository.

The seed also quietly owns `shape` and `size` — `_SEED_OWNED_KEYS` in
`src/observatory/registry.py` refreshes them on a version bump — so a type
restyled through the API silently reverts the next time the seed moves.
Anything set through the API today is provisional.

The shape of a fix, when there is time for it:

- Types live where the rest of the estate's configuration lives, and the seed
  becomes a bootstrap default rather than the source of truth.
- One declaration reaches every cluster, so a type added once is not a
  per-cluster chore.
- Whatever an operator sets through the API survives an upgrade, including
  presentation — or the API stops offering to set things the seed will take
  back.

Until then: add types through the API for anything cluster-specific, and
accept that presentation changes are provisional.

## Open questions

- Which side owns identity when both report the same machine? Probably the
  steward, with the gateway as the fallback — but nothing enforces that today.
- A steward that stops publishing should age its equipment out rather than
  have it vanish. The inbox has TTL semantics; whether "last seen 4m ago"
  reads better than removal is unresolved.
