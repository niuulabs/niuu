# Developer delivery workflows

Developer Delivery accepts a ticket and repository target through **Workflows →
Developer runs**. Its YAML definition lives alongside the other bundled workflows
in `src/ting/workflows/`. Each workflow has its own file. Persona and child-workflow
dependencies are pinned by identity, revision, and content digest; an exported
bundle includes their recursive dependency closure.

## Execution and communication

The root graph contains planning, three plan-review perspectives, coordination,
integration, and publication stages. The coordinator proposes a dependency graph
of work orders. Ting reserves the entire generation and its budget before
launching children. The bundled expansion contract permits up to 100 workstreams;
the active-child limit bounds simultaneous execution independently of that count.

All children start from the same frozen base commit. Dependency edges order
dispatch and integration; they do not copy predecessor edits into a child checkout.
The coordinator must group source-coupled changes into one workstream and split
only changes that can be implemented and tested independently against that base.

Each child runs the Developer Workstream graph: coder, verification, independent
code/security/adversarial reviews, and acceptance or repair. The coordinator has
typed execution and delivery tools; its persona does not have file-editing, shell,
or raw Git tools. Provider selection comes from the caller's configured source
control integration, not from persona instructions.

Local workflow flocks use responsive mode: graph and directed collaboration inputs
drive their turns, rather than autonomous resident inbox observations. Standalone
resident flocks retain their configured autonomy. For Codex-backed execution,
explicit read-only persona permissions also select the native read-only sandbox,
even when workload defaults allow writes. The required Ravn MCP tool server must
initialize successfully before the model can begin a turn.
Codex authorizes the generated `ravn-tools` server with an explicit list of the
persona's filtered tools. Native approval remains `never`; shell, delegation,
account apps, plugins, and tool suggestions are disabled for read-only personas.

Read-only Claude SDK and subprocess execution disables native tools and native
delegation, retaining only the persona's generated Ravn MCP tools. Unsupported
native transports reject read-only execution explicitly. Workload defaults cannot
reintroduce broader permissions or additional MCP servers. The Claude SDK adapter
exposes `mcp_startup_timeout_seconds` and `mcp_status_poll_interval_seconds` as
transport kwargs for bounded required-tool readiness checks.

On workflow-room restart, Skuld restores authenticated structured review outcomes
from the existing durable event log before accepting completion. Missing,
non-boolean, or conflicting validation evidence cannot become a trusted review
through replay. Startup fails if that configured replay is unavailable or exceeds
its completeness bounds. Historical reviews remain auditable; terminal child
evidence selects the current attempt and exact candidate commit and tree.

Ravn's existing A2A client sends child tasks and preserves task/context handles.
Ting records launch reservations, attempts, leases, messages, observations, and
joins. Coordinator replies to children waiting for input use the existing A2A
continuation path. After the verified join, Ting publishes a correlated workflow
event into the exact parent session's existing mesh interface. No polling by a
model or interpretation of a chat message is needed to route that continuation.

Pending child questions and gates retain their exact continuation identifiers in
the durable child record and coordinator notification. Question replies name
`requestId`; gate decisions name `gateId` and `gateDecision`. Replies also bind the
current child attempt and reuse the same message ID when retried. Missing, mixed,
or stale identifiers are rejected. The Developer runs page displays outstanding
questions and gate instructions alongside the child state.

A retry keeps the workstream's persisted allocation so a repair can continue in
the same checkout. Before reusing that checkout, Ting reads the exact blocked A2A
task. A terminal remote task proves its writer has stopped; Ting does not reinterpret
a remotely completed task as accepted evidence. An active task must return a
terminal cancellation acknowledgement. A read or cancellation error, or a
nonterminal response, prevents creation of the next attempt. Failed and canceled
ledger attempts are already terminal. Every retry receives a new attempt ID, and
all candidate, verification, review, and integration receipts must name that new
attempt. Receipts from the superseded attempt cannot satisfy the retry.

## Deployment requirements

Delivery is disabled by default in Forge. Enabling `delivery.enabled` requires
explicit dynamic adapters for workspace operations, evidence authentication, and
delivery authorization, plus acceptance policies and trusted evidence producers.
The default provider adapter resolves the current user's existing source-control
integration for each operation. Do not put provider credentials in workflow YAML.

`LocalGitWorkstreamRepository` needs permitted repository roots, a workspace root,
an evidence root, and named verification contracts. A verification contract pins
a container image by digest, an argument vector, and any trusted repository inputs
by Git blob ID. Verification runs in a restricted container rather than inheriting
the service's source-control credentials. Signing keys belong to trusted services;
they must not be mounted in coder workspaces. Public-key trust also pins which
producer identities each key may represent.

For a container that mounts only its assigned checkout, configure
`isolation_mode: clone`. This creates an independent Git directory with no shared
object-store path dependency. The default `worktree` mode requires the Git common
directory to remain accessible wherever the checkout is used. Integration fetches
the exact accepted commit between isolated clones before validating and applying it.

Ting's `workflow_execution` configuration controls claim/reconcile batch sizes,
lease duration, polling interval, launch budget, and deadline for any workflow
execution. The code delivery specialization's own settings — including the
evidence policy identifiers — live under `workflow_execution.delivery` and
only apply once `workflow_execution.delivery.enabled` is true.
`workflow_execution.delivery.evidence_policy_id` selects child acceptance;
`workflow_execution.delivery.integration_policy_id` selects final integration
acceptance. Configure the final policy with the required remote check names and
trusted forge producer identities.

The parent coordinator can read those configured requirements through
`delivery_evidence.describe` before allocating workspaces. This returns policy
requirements and producer identifiers; callers cannot replace the deployed policy.
The `workflow_execution_retry` tool requires the exact current child attempt ID
and uses the same bounded retry service as the operator API. Repairs inside an
active child task retain that task's attempt ID.
Scoped workload credentials bind delivery operations to their execution. Child
credentials cannot allocate other workstreams, publish branches, or merge.
Developer child sessions using allocated local mounts also exclude source-control
integrations before session contributors receive them; model integrations remain
available. Branch publication is restricted to the persisted integration allocation's
branch and cannot target the execution's base branch.

Developer coordinators use rotating, short-lived workload bearers. Volundr derives
the owner, tenant, and actual Forge session ID from the durable session record, then
uses the existing workload identity issuer to mint the exact workflow-execution
scope and lineage. Parent credentials bind the execution, parent node, session key,
coordinator, and Forge session. Child credentials additionally bind the persisted
attempt, launch intent, A2A task, and the child campaign's Forge session. Descriptor
metadata cannot supply the owner, tenant, admission roles, or actual session claim.

`workflow_execution_credentials` selects the projection adapter and rotation
interval. The file adapter supports local, process, and Docker runtimes. It writes a
mode-0600 bearer with atomic replacement inside a per-session mode-0700 directory;
Docker mounts that directory read-only into every Ravn container, so replacing the
file does not pin an obsolete inode. Configure its owner UID and GID to match the
session runtime. The Kubernetes adapter creates or patches a per-session Secret and
mounts its directory read-only; the Volundr chart grants the conditional Secret
permissions needed for creation, rotation, and terminal cleanup. Restart recovery
reprojects active sessions, and terminal sessions remove their projected credential.

Ravn reads the bearer file for each request through the existing file-backed HTTP
authentication adapter. It does not copy the bearer into model input or generated
configuration. Developer launches reject obsolete literal `auth_token` or
`auth_token_file` configuration before persisting a session. Generic workflows scrub
those obsolete developer-only fields without inheriting their authority. There is no
static-token fallback: an authenticated developer launch fails before session
creation when the issuer, trusted signing key, projection adapter, runtime support,
or mount is unavailable. The configured refresh interval must be shorter than the
workload token lifetime.

Gateway admission roles come only from operator configuration. The scoped route
guard still restricts these bearers to the explicit A2A or developer-coordination
operations, and Ting verifies the execution and Forge-session lineage again before
each mutation or delivery authorization. A child credential cannot become a parent
coordinator credential or perform publication and merge operations.

The local Docker rotation probe exercises a real read-only directory mount and
observes an atomic bearer replacement from the configured session UID. Kubernetes
Secret projection and RBAC have automated adapter and chart coverage, but have not
been exercised by the current live proof on a Kubernetes cluster. The current
developer-delivery proof also uses explicitly anonymous operator ingress, so it does
not establish live Envoy/Cedar admission behavior even when it exercises rotating
workload credential files. Authenticated OpenShell projection is unsupported and
fails closed; use Docker/local/process or Kubernetes for authenticated developer
coordinators.

The runtime receiving a child must support its allocated workspace. A local
filesystem path is not transferable to an SSH VM merely by putting it in A2A
metadata. The current SSH VM runtime rejects host-path sources. Use a compatible
runtime and mounts, or a complete remote workspace transport that returns the
exact resulting commits to the trusted workspace service.

Configure the workstream adapter's `repository_paths` mapping from each exact
repository URL to its canonical local checkout. The coordinator can then omit
`repository_path` when allocating a workspace. Mapped checkouts must exist below
`repository_roots`; an explicit path must agree with the mapping when one exists.
This keeps host paths in deployment configuration rather than portable workflows.

The target forge also needs a real CI runner and pipeline. Strict publication
requires a server mechanism that serializes the candidate against its tested base.
The GitLab adapter requires merge trains, merged-result pipelines, and mandatory
successful pipelines. Missing capabilities block publication rather than allowing
a direct push to the target branch.

If a GitLab enqueue succeeds but its response is lost, use the existing read-only
Forge reconciliation operation. With no recorded provider operation ID, the adapter
requires one unambiguous merge-train entry for the exact review and target, then
verifies the source, base, method, pipeline, ancestry, and canonical merge result.
An explicitly supplied operation ID must match. Retrying the merge operation still
requires fresh preflight; it does not bypass a target that has already advanced.

When checks or a queued merge are pending, the coordinator registers a wait with
`workflow_execution_wait` against the graph's pinned `wait` node, then yields to it.
The call names one of the node's own declared `conditions` (`forge.checks`,
`forge.merge`, ...) plus a condition-specific `request` identity — the tool call
itself is `{nodeId, conditionType, request}`. Ting persists the wait and uses the
existing developer execution worker to poll it through whichever
`WaitConditionObserver` is registered for that exact condition type in Ting's
`workflow_execution.wait_observers` configuration; the generic wait machinery never
interprets what the condition means. Terminal observations resume the parent
through `developer.delivery.observed`; no model turn is needed while waiting.
A `forge.checks`/`forge.merge` request binds the repository, review, source commit,
target commit and branch, and configured integration policy; a merge wait also
binds the merge method. Owner-scoped wait history is available at
`GET /api/v1/ting/workflow-executions/{execution_id}/waits`.

Integration-review or CI failures route to an executable coordinator repair
stage. It reconciles the recorded plan revision and creates a complete replacement
generation from the frozen base, including any unchanged requirements. Reserving
that generation clears the previous integration candidate and review evidence.
Completed children are historical evidence, not retryable failed attempts. A
resumed coordinator can recover its allocation and candidate through reconciliation
and rerun configured verification contracts to obtain current signed receipts.

## Evidence and completion

Child results alone do not satisfy the join. Acceptance checks the persisted
attempt, workspace identity, repository, base, candidate commit/tree, requirement
coverage, verification receipts, and independent reviewer receipts. Signatures
must come from the producers pinned by the selected policy. Superseded attempts
cannot satisfy a newer generation.

Integration inspection verifies an ordered chain of signed receipts from the
original base to the final candidate. That chain must include every accepted
current child attempt. The integration reviewer receives the exact candidate
patch through a read-only delivery tool. Ting attests the outcome from the known
runtime reviewer identity and binds it to the inspected commit and tree. The
pre-merge authorization checks this recorded candidate and review before any
remote merge operation; choosing another policy cannot bypass the execution's
configured integration policy.

`POST /api/v1/ting/workflow-executions/{execution_id}/complete` accepts a merge
request and integration evidence, not a caller's assertion that a merge happened.
Ting validates the evidence through Forge and asks Forge to reconcile the actual
remote publication. The result must match the execution, repository, target
branch, base SHA, source SHA, review number, and merge method. Ting then atomically
checks that the execution and successful child join have not changed before
persisting completion and the merge receipt. Cancellation or concurrent changes
prevent that transition.

Run detail and the evidence endpoint retain task handles, attempts, failures,
validation reports, and final merge evidence. The UI exposes cancellation,
reconciliation, retry, and the evidence document. A passing mocked HTTP test is
evidence for the interface contract, not evidence that a live coding run merged.

Select a run and choose **View evidence** to open its rendered Markdown report.
The report includes the current generation's latest workstream attempts, contract
verification receipts, specialist reviews, requirements, integration, publication,
and outstanding blockers. Historical attempts remain separately identified.
Durable remote delivery observations include check results, provider detail links,
and merge state. Publication, CI, and merge are reported separately and matched to
the current generation's integration candidate; older observations remain labeled.
**Copy Markdown** and **Download Markdown** export the same content; the raw
execution and evidence responses remain available in the disclosure below it.
Active reports refresh automatically and fetch a final snapshot at termination.

Server acceptance in this report applies to current child evidence. A signature
marked present is not a claim that the browser independently verified it. Remote
CI and merge remain unproven until the corresponding evidence is recorded.

Other workflow views can reuse the exported `WorkflowResults` component from
`@niuulabs/plugin-ting`: supply Markdown, a title, status, and optional context
items. The renderer uses the existing shared Markdown component and does not
depend on a repository provider or developer-workflow schema. The separate pure
`buildWorkflowExecutionResultsMarkdown` function projects developer execution
responses into that generic presentation contract.

Use the read-only proof verifier to export and check a completed execution from its
three public Ting evidence surfaces. A trust decision is mandatory: pass either
`--evidence-trust-file` (independent cryptographic verification) or
`--trust-server-attestation` (explicitly accept the server's own report instead).
Passing neither is a usage error — the tool refuses to run rather than silently
report `status: "verified"` with zero signatures actually checked:

```bash
python scripts/verify_delivery_proof.py \
  --base-url http://127.0.0.1:8180 \
  --execution-id 00000000-0000-0000-0000-000000000000 \
  --trust-server-attestation \
  --output developer-delivery-proof.json
```

For authenticated deployments, pass `--token-file` with a bearer token file. The
tool never reads a token from an ambient environment variable and never includes it
in output. It performs only HTTP `GET` requests and exits nonzero for incomplete,
rejected, unsigned, stale, or identity-mismatched evidence. With
`--trust-server-attestation` and no trust file, the report's `status` is
`"server-attested"` — never `"verified"` — and it records
`independentCryptographicVerification: false`: without deployment public keys it
checks signature presence and Ting's persisted validation decision rather than
claiming an independent signature verification.

To verify receipt signatures independently (and reach `status: "verified"`), pass
a public-only trust file instead:

```bash
python scripts/verify_delivery_proof.py \
  --base-url http://127.0.0.1:8180 \
  --execution-id 00000000-0000-0000-0000-000000000000 \
  --evidence-trust-file evidence-trust.json \
  --output developer-delivery-proof.json
```

The JSON uses the same key and producer authorization mappings as the runtime
evidence authenticator, plus an optional `review_producers` role pin. Public keys
are inline PEM strings; private keys, bearer tokens, and other credentials do not
belong in this file:

```json
{
  "trusted_public_keys": {
    "delivery-key-2026": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n"
  },
  "producer_keys": {
    "workstream-runner": ["delivery-key-2026"],
    "developer-code-reviewer": ["delivery-key-2026"],
    "developer-security-reviewer": ["delivery-key-2026"],
    "developer-adversarial-reviewer": ["delivery-key-2026"],
    "developer-integration-reviewer": ["delivery-key-2026"],
    "forge-service": ["delivery-key-2026"]
  },
  "review_producers": {
    "code": ["developer-code-reviewer"],
    "security": ["developer-security-reviewer"],
    "adversarial": ["developer-adversarial-reviewer"],
    "integration": ["developer-integration-reviewer"]
  }
}
```

Every producer found on a receipt must be listed and authorized for that receipt's
key. With a valid trust file, the verifier checks the canonical `evidence_payload`
for every verification, review, integration, and merge receipt and records
`independentCryptographicVerification: true`. A missing signature, altered payload,
unknown producer, or unauthorized key makes verification fail.

`review_producers` is optional, but once present it must pin every review role the
verifier checks (`code`, `security`, `adversarial`, `integration`) — a partially
pinned set is rejected at load time rather than silently leaving a role
unrestricted. It mirrors `niuu.domain.evidence.EvidencePolicy.review_producers`
(role -> authorized producer IDs): a producer that is a trusted signer in general
(its key validates) but is not pinned to a given role is still rejected for a
review under that role. This closes the gap where a `developer-code-reviewer` key
could otherwise sign off a `security` review just because its signature is
cryptographically valid.

## Verification

Use `make verify` for the backend checks and coverage gate. In `web-next`, run
`pnpm test`, `pnpm lint`, `pnpm typecheck`, and `pnpm build`. The developer-workflow
Playwright spec exercises launch and run controls against explicit test fixtures.
Live acceptance must additionally run a ticket through real model sessions,
capture the child task and signed receipt lineage, and record the remote pipeline
and resulting target commit. Repeat with a failing test and repair before treating
the workflow as proven end to end.
