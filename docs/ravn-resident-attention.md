# Resident Attention

Status: proposal. Supersedes earlier drafts.

## 1. What is actually wrong

Three separate statements, in order of how much they explain:

1. **A telemetry stream is being fed into an attention queue.** Ivaldi receives
   every tick of a machine reporting its own state — progress, temperature, task
   phase. A print at 41%, 42%, 43% is state being *published*, not events that
   happened. State should be read; events should be queued. The system currently
   has only the queue, so everything goes there.
2. **The queue is also the archive.** One markdown file per tick, retained
   forever while unjudged, scanned on every write. 84,066 files.
3. **Judgment is not reusable.** Ivaldi can conclude a family of observations is
   routine and promote that as prose, but nothing turns it into behaviour.

The first explains the volume, the second explains the IO and the growth, the
third explains why it never recovers. They need different fixes and the first two
do not need any learning system.

## 2. The shape of the fix

> **The archive is append-only and complete. The queue is coalescing and
> bounded.**

Every raw observation is written, forever, to a cheap append-only log that is
never read on the hot path. The *queue* holds at most one slot per distinct
observation shape per source. A new observation folds into its slot, updating a
count and aggregates, until the resident looks at it.

This is a coalescing queue — a standard, entirely domain-neutral data structure.
It makes no claim about what any signal means. It only says: the resident has
not looked at the previous observation of this shape yet, so it does not need a
second queue slot.

The property that makes it right: **it only collapses what the resident has not
yet seen.** When the resident is keeping up, the coalescing rate is zero and
nothing is collapsed. When it falls behind, the queue stays bounded instead of
growing. The collapse rate is a direct measure of how far behind it is, which
makes it a metric worth watching rather than a behaviour to be nervous about.

## 3. Stage 1 — check what Ivaldi is subscribed to

Config, not code. Hours, not weeks. Do this before anything else.

The NATS adapter takes `subject` as a constructor kwarg and
`SignalSourceConfig.kwargs` passes it straight through, so narrowing what a
resident watches is a configuration change available today.

This is legitimate and is not the operator authoring a filtering rule. Two
different things get conflated:

- *"Temperature telemetry is unimportant"* — a claim about meaning. The
  resident's call. Operators must not write this.
- *"This resident is responsible for job lifecycle, not sensor telemetry"* — a
  job description. The operator's call, always.

A steward whose mandate is "everything the machine ever says" is misconfigured,
and no amount of learned attention repairs a bad mandate.

## 4. Stage 2 — archive and coalescing queue

Scope: `LocalResidentInbox` and the resident-home selection path. The
`ResidentInboxBackend` port is unchanged. Mimir is out of scope — it already
throttles retention off the write path, and atomic rename is a local-filesystem
operation that does not transfer to page semantics.

### 4.1 Layout

```
resident/inbox/raw/YYYY-MM-DD.ndjson   append-only, complete, never on hot path
resident/inbox/pending/<shape>.md      one coalescing slot per shape, bounded
resident/inbox/processed/<...>.md      judged slots, normal retention
```

Date-partitioned NDJSON replaces one-file-per-tick: a single append per
observation, no directory growth, no glob, no parse, trivially compressible and
trivially greppable — which is what "raw evidence remains searchable" should
have meant all along. 84,066 files become roughly one per day.

### 4.2 Shape key

Derived, never configured, never inferred from meaning:

```
shape = (source_id, kind, sorted field-path set, per-path value types)
```

Two observations share a slot only if they are structurally identical. A payload
that gains, loses or retypes a field gets its own slot — so schema drift and
novel structure surface immediately rather than being folded away.

### 4.3 What a slot carries

- `observation_count`, first and last observed time
- first and last raw archive offsets (the exact range this slot covers)
- per-numeric-path min/max
- per-categorical-path distinct value set, up to a configured cardinality cap;
  above the cap the path is recorded as high-cardinality with the cap noted
- the newest full payload, plus the payloads at each numeric extreme

Aggregates only summarize *within* an identical shape. They never merge across
shapes and never decide relevance.

### 4.4 Ingestion

Append to the archive, then upsert the slot. No scan, no parse of other
records, no lock held over a directory walk. Cost is independent of history size
and of pending size.

### 4.5 Acknowledgement

Listing captures each slot's `last_offset`. Acknowledge is a compare-and-set:

- unchanged since listing → move the slot to `processed/`;
- advanced since listing → write a `processed/` record covering the judged
  range, and rewrite the slot to cover only the newer delta.

The resident therefore always acknowledges exactly what it saw, and observations
that arrived mid-turn are never silently swallowed. No lease, no claim record,
no expiry — the slot *is* the boundary.

### 4.6 Selection, retention, invalid outcomes

- Selection is oldest-slot-first, bounded; operator-directed messages bypass.
  With a bounded queue, fairness is no longer an interesting problem.
- Retention reads `processed/` only. It never opens a pending slot and **never
  touches `raw/`** — that exclusion is an invariant with its own test, because
  JetStream retains roughly two hours and the archive is the only surviving copy
  of this history.
- Invalid outcomes are *completed* turns, not failed enqueues. Count attempts in
  the invalid-outcome completion path after the turn record is durable; on
  exhaustion mark the slot `BLOCKED`, keep all evidence, and emit one bounded
  operator notification. A metric alone is not enough — a slot that can never be
  judged needs a human.

### 4.7 Migrating the existing 84,066

One-time, resumable, non-destructive, operator-invoked:

1. Stream every existing file into the raw NDJSON archive in observed order.
2. Rebuild slots from the archive; judged records go to `processed/`.
3. Reconcile: every one of the 84,066 accounts for exactly one archive record.
4. Only then remove the old flat files.

The pending queue afterwards is a handful of slots, not 82,900 records — so
there is no backlog to drain and no archive-versus-drain decision to make.

### 4.8 Acceptance and tests

- Write cost independent of archive and queue size at 100k observations.
- Pending slot count bounded by distinct shapes, verified under sustained
  ingress with no resident running.
- Retention never opens a pending slot and never touches `raw/`, at any setting.
- Acknowledge is exact under concurrent arrival (the compare-and-set case).
- Schema change creates a new slot rather than folding.
- High-cardinality categorical paths degrade to a marker, not unbounded growth.
- Migration is resumable and reconciles every record; no file is deleted before
  reconciliation succeeds.
- Attempt bound triggers on invalid outcomes, not enqueue failures, and
  escalates once.
- Archive remains greppable after migration.

## 5. Stage 3 — learned attention, with receipts

Only after stage 2, and only if telemetry shows the resident repeatedly reaching
the same conclusion about the same shapes.

### 5.1 The design difference

A matched slot is **not** suppressed silently. It does not create a turn, but its
aggregate appears in a receipt block on the resident's *next* turn regardless:

```
Since you last thought:
  pattern P (v3, expires in 4h): 3,412 observations across 2 shapes
    progress 0–100, temperature 31.4–58.9, phases {heating, printing, cooling}
    2 observations outside the pattern's ranges — payloads attached
```

The resident sees everything the pattern suppressed, in aggregate, every time it
thinks. This is what makes the rest small:

- **No shadow mode as a separate stage.** Replay plus a short expiry plus
  receipts already gives live feedback from the first activation.
- **No sampling machinery, no health floors.** Coverage is 100%, not a sampled
  fraction. The failure mode both earlier designs spent the most machinery on —
  a pattern destroying the evidence that would falsify it — cannot occur.
- **Contradiction is ordinary judgment.** The resident reads a receipt and says
  the range is wrong. That is the mechanism; there is no protocol.

A pattern that misbehaves is visible in the next receipt, not in a dashboard
nobody reads.

### 5.2 Evaluation point

Patterns are evaluated **per slot at selection time**, never per observation at
ingestion. Coalescing has already done the volume work; the pattern only decides
whether a bounded slot needs a turn. Fewer evaluations, richer input (the slot
carries ranges and distributions), and nothing added to the ingestion path.

### 5.3 Record

Stored via the existing resident-state port, as a typed record beside
`ResidentPolicyObservation` (which stays as it is).

```
pattern_id, version, matcher_hash
resident_id, environment_id
matcher
expires_at                              # mandatory, bounded by config
authored_by_case_id
evidence_positive, evidence_negative
observed_field_paths, observed_numeric_ranges
retired_reason, blocked_matcher_hash
```

There is **no effect field** — not an enum with one value. The single possible
consequence of an active pattern is that a matching slot does not create a turn.
Counters live in metrics and receipts, never in the artifact.

### 5.4 Matcher

Operators: `equals`, `in`, `present`, `absent`, `numeric_between`, composed only
by `all_of`. No regex, no free-text or summary matching, no `any_of`, no
unbounded `not`, no expressions.

Mandatory fail-open rules — any of these means *no match*, which means the
resident wakes:

1. Field paths must come from observed positive or negative evidence.
2. An unknown incoming field path.
3. A missing expected field.
4. A type mismatch or parse failure.
5. A numeric value outside the evidence-derived range. Because patterns match
   slots, this tests the slot's min/max — a single excursion inside a bucket of
   ten thousand normal readings still wakes the resident.
6. Fields present only in counterexamples become mandatory absence constraints.
7. Matcher size and depth are bounded.
8. Transport metadata — event id, offsets, timestamps, trace context — is
   excluded from the matchable document.
9. Directed operator messages are structurally ineligible.

Rule 5 is what keeps a temperature excursion from being swallowed. Rule 6 is
what keeps `error` a wake condition without anyone writing down that errors
matter.

### 5.5 Lifecycle

`active → retired`, plus expiry derived at evaluation time so no sweeper exists.
Quarantine is retirement with a blocked matcher hash.

- **Authoring:** post-session reflection, which is confirmed to process
  resident-home sessions. The model authors matcher content only; Ravn owns
  validation and enforcement.
- **Validation:** replay against the archive. A candidate must match nothing the
  resident acted on or escalated, and nothing in its own negative evidence.
- **Activation:** short mandatory expiry. Renewal requires a fresh resident
  judgment on a receipt, not a timer.
- **Retirement:** immediate on contradiction, with the matcher hash blocked so
  the same matcher cannot be re-proposed unchanged.

### 5.6 Tests

Operator truth tables; every fail-open rule; a single out-of-range observation
inside a large matching slot still wakes the resident; negative evidence never
matches; stable serialization hash; complexity limits reject oversized matchers;
the type cannot express an action or authority change; expiry is lazy;
contradiction retires within one turn; a retired hash cannot be recreated;
**every suppressed slot appears in the next turn's receipt**; the existing
stewardship wake still fires under total suppression.

Replay fixture from the archive: routine observations, task transitions, schema
differences, temperature boundaries, error and anomaly examples, and existing
ignore / non-ignore judgments.

## 6. Where judgment lives

The model authors matcher content, from its own prior judgments, and nothing
else. Runtime code owns coalescing, evaluation, fail-open rules and expiry — all
domain-neutral mechanism. Operators choose what a resident watches; they author
no rule about what any signal means. No file in the tree should contain a
sentence resembling "status signals are routine."

Placement: a pure evaluator in Ravn above the inbox adapter. Nothing in
`LocalResidentInbox`, `MimirResidentInbox`, Niuu, Skuld or Volundr. Storage
adapters persist outcomes; they do not decide attention.
`ResidentLearningRuntime` does not install these as skills.

## 7. Not doing now

- **Broker-side last-value-per-subject.** JetStream can collapse state streams
  before Ravn sees them (`max_msgs_per_subject`, or a KV bucket). It is the
  cheapest possible version of this and remains worth doing — but it needs the
  producer and the stream config to change, so it complements agent-side
  coalescing rather than replacing it. Agent-side works for every source
  regardless of what its producer can do.
- **A nano-tier triage model.** Attention triage is a far smaller decision than
  resident judgment; a nano model or embedding check could consider every slot
  cheaply and escalate only on novelty. More in the spirit of "Ravn owns
  judgment" than a frozen matcher, but it is a bigger change and stage 2 may
  make it unnecessary.
- Matcher language beyond the six operators; cross-resident or Flock sharing; a
  policy service, installer or registry; new APIs or dashboards; a judgment
  database; queue leases; multiple pattern effects; authorization integration.
- `classify.py` is an existing hand-written keyword table mapping domain terms to
  classifications. Out of scope, but do not extend it.

## 8. Risks

- **Coalescing hides a transition between two routine observations.** Mitigated
  structurally: a differing field-path set never folds, numeric extremes keep
  their full payloads, and the raw archive keeps everything. Worth an explicit
  test against real Ivaldi transitions.
- **Archive loss.** `raw/` is the only surviving copy — JetStream holds ~2h.
  Every retention path must exclude it, by construction rather than by config.
- **A >2h Ravn outage loses raw signals outright**, since JetStream is the only
  upstream buffer at 32 MiB. Out of scope here; raise it with whoever owns the
  stream configuration.
- **Homogeneous evidence.** Many near-identical observations are not many
  independent ones. Rules 5 and 6, plus requiring evidence across distinct cases
  and time windows before synthesis.

## 9. Verified facts

- Ivaldi uses `LocalResidentInbox`.
- 84,066 signal files; ~1,200 `remembered`.
- Eitri wakes with 50 signals per turn.
- Laevateinn payloads carry no explicit event id, so the generic adapter falls
  back to hashing the full changing payload — every tick becomes unique.
- Post-session reflection does process resident-home sessions.
- JetStream is capped at 32 MiB, retaining roughly two hours at observed rate.
- `MimirResidentInbox` already throttles retention off the write path;
  `LocalResidentInbox` sweeps synchronously on every write.
- `SignalSourceConfig.kwargs` passes `subject` to the NATS adapter, so
  subscription scope is already configurable.
