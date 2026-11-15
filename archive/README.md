# Archived catalogue

Bundled Ting workflows and built-in personas that are **not shipped**: not in the
wheel, not in the container images, not in the Nuitka binary, not in the Helm
charts, and not served or seeded by Ting or the persona registry. The files are
kept here byte for byte so they can be restored.

Nothing under `archive/` is read at runtime. The shipped code carries the list of
what is archived (`ARCHIVED_SYSTEM_WORKFLOW_IDS` in `src/ting/system_workflows.py`,
`src/personas/archived-personas.txt`), and anything that is
kept and still names an archived item stops at startup. `tests/test_catalog_archive.py`
keeps the lists, the archive and the packaging in step.

## What is here

### What still ships

* Workflow: `code-review-merge` (Code, Review and Merge). The coder implements, the
  reviewer and the security auditor check the change in parallel, and the coder merges
  once both pass and a person approves the merge. Either check, or the person, can send the work
  back to the coder, with no round limit.
* Personas: `coder`, `reviewer`, `security-auditor`.


### `workflows/`

| File | What it was for |
|---|---|
| `code-review-flow.yaml` | Tracker-driven developer workflow that implements a ticket, iterates through code review, and merges the branch without publishing a delivery record. |
| `developer-delivery.yaml` | Plan, delegate, verify, integrate, publish, and merge one repository change using durable child workflows and typed evidence. |
| `developer-integration.yaml` | Verify and review the complete integration candidate against the approved delivery plan. |
| `developer-planning.yaml` | Investigate a delivery request and produce an exact-revision plan approved by three independent reviewers. |
| `developer-workstream.yaml` | Implement one isolated work order, run trusted verification, and independently review its immutable candidate. |
| `infra-change-flow.yaml` | Tracker-driven change to a GitOps repository. |
| `research-campaign.yaml` | Flexible deep-research workflow that frames a question, explores the evidence deeply through parallel threads, challenges the thesis, and distills durable memory into Mimir. |
| `research-thread-breadth.yaml` | Run the breadth explorer over the framed research question and produce a structured finding for the parent research campaign. |
| `research-thread-contrarian.yaml` | Run the contrarian explorer over the framed research question and produce a structured finding for the parent research campaign. |
| `research-thread-depth.yaml` | Run the depth explorer over the framed research question and produce a structured finding for the parent research campaign. |
| `research-thread.yaml` | Run one exploration thread on an assigned angle and produce a structured finding for the parent research campaign. |
| `reviewed-delivery-flow.yaml` | Tracker-driven developer workflow that implements a ticket, iterates through agent review, then waits for a person to approve the merge before it merges the branch and publishes a durable delivery record. |
| `saga-planning.yaml` | Focused planning workflow that clarifies a brief, drafts a saga breakdown, and pauses for review before publishing the plan. |
| `specification-stack.yaml` | End-to-end specification workflow that produces a PRD, SRD, SDD, and delivery breakdown with explicit review and approval gates between each artifact. |
| `ticket-planning-flow.yaml` | Plans one tracker ticket, has an agent review the plan, then waits for a person to approve it before recording the approved plan for the work that follows. |
| `ticket-work-flow.yaml` | Does the work one tracker ticket asks for, with or without a repository, then waits for a person to approve the result. |
| `tool-skill-builder.yaml` | Resident capability workflow that designs, implements, reviews, canaries, and publishes a missing Valkyrie/Ravn tool or skill. |
| `tracker-delivery-flow.yaml` | Tracker-driven developer workflow that implements a ticket, iterates through code review, updates the tracker, merges the branch, and publishes a durable delivery record. |

### `personas/`

| File | What it was for |
|---|---|
| `capability-publisher.yaml` | Publishes a finished capability build's learned_tool.json to Mimir after review and security have passed, or reports it blocked; talk to it only as the last step of a capability build. |
| `closer.yaml` | Merges reviewed code into the target branch, updates the tracker ticket and writes the closure record; talk to it once a review has passed and the change is ready to land. |
| `developer-adversarial-reviewer.yaml` | Tries to break an immutable candidate commit with counterexamples, boundary inputs and failure cases without changing it; talk to it for a hostile read of a verified candidate. |
| `developer-analyst.yaml` | Turns a ticket and the current code into a revisioned delivery plan with requirement IDs and acceptance tests, and revises it against reviewer findings; talk to it to plan or re-plan developer work. |
| `developer-architecture-reviewer.yaml` | Reviews one developer plan revision for boundaries, interfaces, decomposition, migration and operational ownership, read-only; talk to it for an architecture opinion on a plan. |
| `developer-code-reviewer.yaml` | Reviews an immutable candidate commit's diff for correctness, maintainability, scope and requirement coverage, read-only; talk to it for a code review of a verified candidate. |
| `developer-coder.yaml` | Implements one durable work order inside its allocated worktree and path scope and reports the candidate commit; talk to it to carry out a single bounded workstream or repair. |
| `developer-coordinator.yaml` | Splits an approved developer plan into a dependency graph of bounded work orders and drives them to delivery through workflow tools, without editing source; talk to it to coordinate a delivery, not to write code. |
| `developer-correctness-reviewer.yaml` | Reviews one developer plan revision for requirement coverage, edge cases, test oracles and failure modes, read-only; talk to it for a correctness check on a plan. |
| `developer-integration-verifier.yaml` | Reviews the complete integration candidate against the approved plan and its signed verification receipts before publication; talk to it to judge whether integrated work meets every requirement. |
| `developer-security-plan-reviewer.yaml` | Reviews one developer plan revision for threats, privileges, credential handling, recovery and abuse cases, read-only; talk to it for a security opinion on a plan. |
| `developer-security-reviewer.yaml` | Reviews an immutable candidate commit for exploitability, trust boundaries, authorization and data loss, read-only; talk to it for a security review of code. |
| `postmortem-analyst.yaml` | Reads a finished task's history and writes a concise memory document for ingestion into Mimir; talk to it after a run completes to record what happened. |
| `product-steward.yaml` | Chats with an operator to sharpen a product idea, keeps the initiative's thread in Mimir, and launches research, planning or specification pipelines only when asked; talk to it to shape or follow a product initiative. |
| `publisher.yaml` | Writes a tracker delivery's ticket handoff, implementation summary, review verdict and closure to Mimir as a durable record; talk to it after a delivery has merged. |
| `research-analyst.yaml` | Turns the findings of a research campaign's exploration threads into a structured thesis, noting clusters and conflicts; talk to it once the parallel explorers have finished. |
| `research-coordinator.yaml` | Turns a framed research brief into a bounded set of parallel exploration threads and dispatches them as child workflows without doing research itself; talk to it to start or retry the exploration stage. |
| `research-curator.yaml` | Extracts portable learnings and open follow-ups from a finished research campaign into its notebook; talk to it after synthesis to make the work reusable. |
| `research-explorer-breadth.yaml` | Maps the landscape of a research question widely, finding the main camps, themes and leads; talk to it for a broad survey before anyone narrows. |
| `research-explorer-contrarian.yaml` | Looks for dissent, edge cases and counter-evidence to a research question's obvious answer; talk to it to test the main story from the outside. |
| `research-explorer-depth.yaml` | Digs into the strongest threads of a research question for mechanisms and specifics, separating strong evidence from repetition; talk to it for a deep dive on one thread. |
| `research-explorer.yaml` | Runs one exploration thread of a research campaign from an assigned angle and returns a structured finding; talk to it to explore a framed question from a given angle. |
| `research-framer.yaml` | Turns a rough research prompt into a clear inquiry brief without narrowing it into an engineering ticket; talk to it to start a research campaign. |
| `research-publisher.yaml` | Publishes a research campaign's notebook into Mimir as verified durable artifacts; talk to it at the end of a campaign. |
| `research-skeptic.yaml` | Stress-tests a research thesis for weak reasoning, thin evidence and overconfidence; talk to it to challenge an analysis before it is synthesized. |
| `research-synthesist.yaml` | Turns a challenged body of research into the final deliverable, decisive where the evidence allows and open about uncertainty; talk to it once the thesis has been critiqued. |
| `saga-brief-framer.yaml` | Turns a rough planning prompt and any feedback into a concise planning brief for a human to approve; talk to it to start or rework a saga plan. |
| `saga-plan-publisher.yaml` | Records an approved saga plan as a short note in Mimir; talk to it after a plan is approved. |
| `saga-plan-reviewer.yaml` | Checks that a drafted saga plan is bounded, sequenced and actionable before the human gate; talk to it for a review of a drafted saga structure. |
| `saga-planner.yaml` | Turns an approved planning brief into the bounded saga structure Ting accepts, naming real files in the repository; talk to it to draft or revise a saga plan. |
| `specification-breakdown-critic.yaml` | Challenges a specification's delivery breakdown for missing work, bad sequencing and hidden dependencies; talk to it to review a breakdown draft. |
| `specification-breakdown-planner.yaml` | Turns an approved design into an actionable delivery breakdown; talk to it to draft or revise the breakdown of a specification. |
| `specification-framer.yaml` | Turns a rough initiative prompt into a decision-useful brief to start a specification, without writing the PRD; talk to it to begin a specification. |
| `specification-prd-author.yaml` | Writes the product requirements document from a framed brief; talk to it to draft or revise a PRD. |
| `specification-prd-critic.yaml` | Challenges a PRD for weak product thinking, missing user value and unacknowledged risk before approval; talk to it to review a PRD draft. |
| `specification-publisher.yaml` | Makes an approved specification pack durable in Mimir with a manifest of what landed; talk to it after the breakdown is approved. |
| `specification-sdd-author.yaml` | Turns the approved PRD and SRD into a software and system design document; talk to it to draft or revise an SDD. |
| `specification-sdd-critic.yaml` | Challenges a design document for architecture bloat, hidden coupling and weak rollout thinking; talk to it to review an SDD draft. |
| `specification-srd-author.yaml` | Translates the approved PRD into a system requirements document; talk to it to draft or revise an SRD. |
| `specification-srd-critic.yaml` | Challenges a system requirements document for ambiguous, contradictory or untestable requirements; talk to it to review an SRD draft. |
| `ticket-coordinator.yaml` | Stays the single point of contact for one open tracker ticket, carries its context across workflows and starts a named workflow when told to; talk to it to plan a ticket or ask what is happening on it. |
| `ticket-plan-publisher.yaml` | Records the plan a person approved for a tracker ticket in Mimir so later work can read it; talk to it after a ticket plan is approved. |
| `ticket-plan-reviewer.yaml` | Checks that a tracker ticket's plan is bounded, specific and actionable before a person sees it; talk to it to review a ticket plan. |
| `ticket-planner.yaml` | Writes a short, ticket-specific plan for a person to approve at a review gate, naming real files; talk to it to plan a tracker ticket. |
| `ticket-worker.yaml` | Does the work a tracker ticket asks for, which need not be software (research, writing, a decision), and hands the result to a review gate; talk to it to carry out a ticket that is not a code change. |

### `scripts/`, `tests/`, `docs/`

The developer delivery proof tooling moved with the Developer workflows it proves:
`scripts/prepare_delivery_proof.py`, `scripts/verify_delivery_proof.py`, their tests
in `tests/`, and the operator guide in `docs/developer-workflows.md`. Restoring the
Developer workflows means moving these back too (`scripts/`, `tests/test_scripts/`,
`docs/operator/`).

## Who still names an archived item

These are decisions, not oversights. Each answers a missing workflow with a loud error
that names this archive, and `tests/test_catalog_archive.py` allows exactly these:

* `src/ting/api/research.py`, `specs.py`: the Research Center and Specs pages launch the
  workflow tagged `research` / `specification`, then the one named "Research Campaign" /
  "Specification Stack". A user workflow with the tag still works; with none, the launch
  returns 404 naming this archive.
* `src/ting/api/sagas.py`, `src/ting/domain/services/review_list.py`: saga planning
  launches the system workflow named "Saga Planning". With it archived, `POST .../plan`
  returns 404 naming this archive.
* Resident tool-building: `build_tool` (`src/ravn/adapters/tool_build/a2a.py`) commissions the
  workflow that carries the `tool-builder` tag, which was Tool & Skill Builder. Ting's A2A card
  projects system workflows only, so with it archived a resident's build finds no builder
  and fails at request time. (ymir also holds a user copy of it; a user workflow is not on
  the card.)
* The ticket board and coordinator have no bundled workflow to offer any more; boards take
  system workflows only.
* The delivery feature (`workflowExecution.delivery`) has no default producers or policies;
  enabling it without naming them stops Ting at startup.

## Restore an item

Restoring is a code change, and it has to be complete or the service refuses to
start.

1. Move the file back.
   * Workflow: `git mv archive/workflows/<name>.yaml src/ting/workflows/`
   * Persona: `git mv archive/personas/<name>.yaml src/personas/builtin/`
2. Remove it from the shipped list so nothing treats it as archived.
   * Workflow: delete its id from `ARCHIVED_SYSTEM_WORKFLOW_IDS`
     (`src/ting/system_workflows.py`). It is then no longer in
     `RETIRED_SYSTEM_WORKFLOW_IDS` either.
   * Persona: delete its name from `src/personas/archived-personas.txt`.
3. Re-pin, because the persona digests a workflow pins may have moved while it
   was archived: `uv run python scripts/repin_bundled_workflows.py`.
4. If the workflow's `version` changes, add the new `id@version` to
   `tests/test_ting/data/bundled_workflow_versions.lock.json`. The lock keeps the
   archived versions as history, and `tests/test_catalog_archive.py` fails if an archived
   file no longer matches its locked revision, so restore before editing.
5. Run `uv run --extra dev pytest tests/test_catalog_archive.py tests/test_ting/test_system_workflows.py tests/test_ting/test_bundled_workflow_versions_lock.py tests/test_personas`.

Migration `migrations/ting/000059_detach_archived_system_workflows.up.sql` cleared
the sagas, project boards and ticket coordinators that named the three archived
workflows. Restoring a workflow does not bring those assignments back; assign it
again where it is needed.

## Archive another item

The reverse: `git mv` the file here, add its id or name to the shipped list,
update every test that counts or names it, and delete nothing else. The
`tests/test_catalog_archive.py` tests fail until the lists, the archive and everything that
still names the item agree.
