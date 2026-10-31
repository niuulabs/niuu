# Seeing the flock at the `build_tool` gate

Status: implemented on `feat/ravn-flock-capability-gate`; not deployed.

The `build_tool` gate asks "does a tool for this already exist?" of the local
resident only. Ymir built `k8s_pod_logs_time_window` while
`k8s_pod_logs_since_sa` had run 27,274 times on Valhalla. This note records what
the flock actually delivers and what changed.

## What the live flock showed (2026-10-31)

Measured on all eight `k8s-valkyries` residents with read-only `kubectl exec`.

**Tool proposals do not reach every resident.**

- Ymir's `flock-k8s-events` stream captures `flock.k8s.ymir.>`; every other
  cluster's captures `flock.k8s.>`. Ymir's consumer filters `flock.k8s.>`, so it
  matches nothing from peers. A 45 s core subscription on Ymir did see Noatun and
  Valhalla, so the traffic arrives and the stream drops it. The stream was
  narrowed on 2026-06-06 (`f328d4f0`); the consumer was re-widened the same day
  (`b93587b8`), and the stream was left behind. Ymir has no
  `flock_learning.json` at all.
- Proposals are one event each. Valhalla's 33 MiB stream held 3.6 hours of
  traffic, so a resident down for longer loses them for good.

**The state-based inventory stays home.** `valkyrie.evolution.skill_inventory`
already carries manifest, code and tests for every tool, every 5 minutes. It is
published to `obs.<env>`, and the flock stream only *sources* that locally.
Sourcing does not republish on the subject, so it never crosses clusters. Only
events published to `flock.k8s.<env>.…` do.

**The gap is flock-wide, not just Ymir.** Each resident lacks 29–45 tools active
on a peer. Between 6 and 25 of those are test-bearing tools its ledger has never
heard of.

| Resident | Active | On a peer, not here | Retired here (gate sees) | Declined here | Never seen | Never seen, with tests |
|---|---|---|---|---|---|---|
| Ymir | 5 | 45 | 0 | 0 | 45 | 25 |
| Vanaheim | 14 | 36 | 9 | 3 | 24 | 16 |
| Noatun | 15 | 35 | 15 | 3 | 17 | 12 |
| Jarnvidr | 16 | 34 | 15 | 3 | 16 | 12 |
| Glitnir | 18 | 32 | 11 | 5 | 16 | 6 |
| Eitri | 18 | 32 | 11 | 3 | 18 | 11 |
| Valaskjalf | 20 | 30 | 7 | 4 | 19 | 15 |
| Valhalla | 21 | 29 | 9 | 4 | 16 | 9 |

**A peer's tool cannot simply be reused.**

- 31 of 137 installed copies have no `test_code`, and `_verify_peer_artifact`
  refuses those. `k8s_pod_logs_since_sa` is one: it cannot be adopted anywhere.
  Three residents adopted it in September and retired it as `unrunnable` on
  2026-10-27.
- Past adoptions failed on canary or re-verification 26 times, for reasons that
  only showed on the adopting resident.

**The bar alone would not catch the Ymir case.** Eight capability families were
labelled from name and description before any model ran, then every tool was
asked against every other through the deployed gate on Ymir (50 tools, 1,710
shortlisted pairs, 84 true pairs):

| Bar | Recall | Precision |
|---|---|---|
| 0.3 | 0.89 | 0.74 |
| 0.4 | 0.86 | 0.83 |
| 0.5 | 0.79 | 0.87 |
| 0.6 | 0.73 | 0.91 |

The Ymir pair scores 0.46–0.50 asked the way a build asks it, and 0.65 the other
way round. Four of the six false positives at 0.6 are "list pods" against
`check_pvc_usage`, whose description opens "List pods in a namespace". The bars
were read off this same data, so this is not a held-out result.

## Design

**Carrier: repair the inventory; add no catalog.** The inventory is state, so it
heals after outages and covers tools built before a resident existed. Each
resident now also publishes it to `flock.<domain>.<env>.…` so it crosses. Crossing
every 5 minutes would be about 240 MiB an hour against a 33 MiB stream, so it
crosses on change and on a slower sweep (`flock_inventory_interval_seconds`,
default 1 h, 1.2 MB per sweep across the flock as measured). Every crossing event lists
the sender's whole inventory, so a tool a peer retired disappears at its next
crossing. There is no TTL: a peer that is down keeps its claims, and adoption
re-verifies here regardless.

**Store: what a resident heard.** `flock_peer_tools.json` holds peers' verified
tools, scoped to shared flocks. Missing means nothing heard yet, which is a real
answer. Corrupt raises at startup.

**Judgment: the same port.** After the local gate finds nothing, peer tools go
through `CapabilityMatcherPort` with the revision question, so Ravn owns the
judgment and Niuu only carries events. Local tools are not judged twice. A judge
that cannot answer stops the build, as it does locally.

**What a peer match does.** The two kinds of mistake cost different amounts, so
there are two bars:

| Peer match | Outcome |
|---|---|
| P ≥ `min_same_probability` (0.6), adoptable | Adopt through the resident install pipeline: review, re-verify here, canary here. No build. |
| Adoption held | Say it is held for review. No build; a local build would hit the same review. |
| Adoption refused or failed (no tests, unloadable reach, failed verification or canary, declined before) | Build locally, seeded with the peer's code and the reason. |
| `peer_prior_art_probability` (0.4) ≤ P < 0.6 | Build locally, seeded with the peer's code. |
| Below that | Build as before. |

A false adoption blocks a build, so it keeps the 0.6 bar. A false seed costs a
few lines in a brief, so it can use a lower one. Seeding is not a fallback: "does
it work here?" has been answered, and the build is what was asked for. Seeded
builds keep the requested name, because a proposal under the peer's name would
install over the peer's working copy. They record `peer_capability` in provenance
so curation can later link them.

Matches are tried best first, and the first to reach the pipeline decides: a
verification costs a job, and failing one is recorded. A tool refused on sight
(no tests, for example) costs nothing, so the next one still gets its turn. If
adoption raises rather than decides, the build stops and says so.

## Live check (2026-10-31)

The changed modules ran in memory on the deployed image (`dev-c8ff256b`, whose
files hash-match this branch's base), so nothing was written to a resident. Each
resident's real inventory went through the new publisher, and the gate ran
against a throwaway copy of the resident's tools with the deployed Jev.

- **Publisher.** All eight residents produced 127 crossing events, 1.2 MB in
  total, on `flock.k8s.<env>.valkyrie.evolution.skill_inventory`.
- **Ymir case.** With `k8s_pod_logs_time_window` removed, three runs of the real
  gate named `k8s_pod_logs_since_sa` on Valhalla at P 0.49, 0.50 and 0.55. The
  build went ahead with a 5.9 KB brief holding the peer's manifest and code. The
  gate now sees the tool. It does not stop the build, and could not: the peer
  tool has no tests, and the request adds `until` and `limit_bytes`.
- **Glitnir's `k8s.pods.list_with_age`.** With same-named copies removed, three
  runs skipped `list_pods_in_namespace_sa` (P 0.85, no tests) and took an
  adoptable list-pods tool instead of building. This case found the search-order
  bug the tests now pin.
- **Adoptability on Ymir.** Of 49 distinct peer tools, 27 pass the pre-checks and
  22 have no tests. None were refused for reach.
- **Replay.** Of 16 tools residents built themselves since 2026-10-25, 8 had a
  peer tool judged the same; 7 of those agree with the labels and 1
  (`discover_and_inspect_pod` to `k8s_resolve_resource_name`) does not. This is
  an upper bound, because the replay ignores build order: of two tools built the
  same week, only the later one could have been stopped.

Adoption's review, re-verification and canary did not run live, because they
start a Job in the cluster. They are covered with a fake runner only.

## What this does not do

- It does not stop every duplicate. The Ymir request scores under 0.6, and the
  peer tool cannot be adopted, so it becomes a seeded build rather than no build.
- An adopted tool runs its canary on `{}` unless the peer recorded a sample. New
  builds now record one; existing tools have none, so a tool that needs an input
  may fail its canary here and be declined.
- An adopted tool may lack what the request added. The resident is told to
  re-request with `replace=true`, which builds its next version in place. A false
  adoption followed by `replace` would rebuild over an unrelated tool, the risk
  the local gate already carries.
- Adoption on proposal arrival still skips the gate, so a new tool can still fan
  out beside a peer's equivalent until curation retires one.

## Deployment

1. Ship the image.
2. In `jve/infrastructure`, change `ymir/nats-streams/manifests/stream-resources.yaml`
   `flock-k8s-events` subjects to `flock.k8s.>`. Ymir then starts adopting peers'
   proposals as they arrive, as every other resident does.

Resident `event_types` need no change: that list only decides whether the
subscription attaches, and dispatch filters on the subscriber's own types. Each
resident gains one durable consumer, for its own cluster's inventory subject, and
drops its own heartbeats on arrival.

Ymir's stream hits its 33 MiB limit in about 24 hours today, and capturing peers
shortens that. Noatun's realm `announce` traffic is 8,749 of the 12,615 messages
in Valhalla's stream, which is the first thing to look at if retention matters.
