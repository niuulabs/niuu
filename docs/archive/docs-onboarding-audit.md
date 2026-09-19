# Documentation audit and onboarding rewrite

Official documentation: https://docs.niuu.cloud/

Reviewed on 2026-09-11 against the documentation and current source checkout.
The official site responds successfully, but this review does not establish
that its deployed content matches this checkout. No clean-machine installation
or authenticated first-session walkthrough has been completed in this review.

## Assessment

The getting-started pages describe capabilities without teaching the reader how
to use them. A successful static docs build only proves that the site builds.
It does not prove that someone can reach a working agent from these instructions.
The earlier startup corrections address specific inaccuracies; they do not make
the onboarding complete.

## Findings

| Priority | Evidence | Reader impact | Required change |
| --- | --- | --- | --- |
| P0 | `get-started/introduction.md` puts the first workspace before project configuration. | Readers reach launch without a working authentication/model path. | Put the minimum runtime and authentication setup before launch. Defer optional project integrations. |
| P0 | `first-ai-workspace.md` says “Choose a quick launch preset or create a custom launch” and “Select a safe repo and provider configuration.” | Neither action explains prerequisites, fields, values, or where to configure the provider. | Walk through one actual launch from an empty installation, with exact fields and expected results. |
| P0 | `reference/credentials-and-secrets.md` contains security guidance and links, but no credential creation or runtime authentication procedure. | The first model request can fail even when the platform starts. | Document and prove one authentication path, including credential location, runtime visibility, and a successful request. Treat other runtime/provider paths separately. |
| P0 | `platform up` is foreground; `down` and `status` use process-local state. | Previous guides promised remote stop/status behavior the CLI does not implement. | Startup guides now use Ctrl+C and `/health`. Audit the remaining CLI/reference pages for the same claim. Fixing the CLI itself is separate work. |
| P1 | `first-local-stack.md` overlaps `one-local-platform.md`; `first-ai-workspace.md` overlaps `one-workspace-session.md`. | Instructions drift, and readers cannot tell which path is authoritative. | Keep one canonical tutorial for each outcome. Make the growth overview link to those tutorials. Preserve old URLs with short forwarding pages. |
| P1 | The current UI opens a “Launch pod” dialog from a `+` button titled “Launch a new session.” It has Source, Runtime, and Confirm steps, and an optional “Load launch spec” field. | Generic references to presets or profiles do not tell readers what to click. | Use current labels in steps and explain session, workspace, runtime, model, launch spec, and target before asking readers to choose them. |
| P1 | `configure-project.md` is a five-item checklist without an executable procedure. | “Add credentials” and “define launch presets” simply move the unanswered question to another page. | Replace it with separate task guides: connect a private repository, attach credentials, save a working launch as a reusable spec. |
| P1 | `model-routing-step.md` asks readers to configure providers in “platform settings or service configuration”; it offers no complete config or successful request. | Readers must reverse-engineer setup. | Provide one complete, validated provider setup with exact config location, secret binding, model selection, verification, and failure diagnosis. Review its fallback/failover claims against current behavior. |
| P1 | The README used the old GitHub Pages hostname. `mkdocs.yml` and `scripts/build_docs_bundle.py` also retain that hostname. | Public links and generated canonical metadata disagree with the official docs address. | README links now use the official domain. Align site metadata and preview URL rewriting together when updating the publishing configuration. |

## First tutorial to build

Outcome: from a clean supported machine, start Niuu, authenticate one agent
runtime, launch one workspace, ask for a small change, inspect the result, and
stop the session and platform.

Use local mini mode first. Select the runtime only after proving its complete
authentication path on that mode; the current mini preflight checks for the
Claude binary, which by itself does not prove authentication or session success.

The tutorial must contain:

1. **A tested setup.** State OS/architecture, Niuu version or commit, runtime
   version, required tools, and the model/provider used. Distinguish a release
   installation from a contributor checkout.
2. **Install and start.** Copyable commands, actual configuration location,
   the initialization choice, expected startup output, and a health check.
3. **Authenticate the runtime.** Exact setup steps and verification that the
   session process can access the credential. A working CLI on the host is not
   sufficient evidence that a sandbox receives the same authentication.
4. **Launch without a saved spec.** Use the session list's `+` button, then
   show Source → Runtime → Confirm. Give explicit values for the chosen example,
   explain which optional fields to leave empty, and identify the `forge session`
   and `open pod` actions. Include screenshots from the tested run.
5. **Do observable work.** Supply one small prompt with a deterministic file
   result. Show where to find that file and, for a Git workspace, its diff.
   Verify the result independently of the assistant's response.
6. **Stop and recover.** Show how to stop the session, what persists, how to
   reopen the work, and how to stop the platform. Include the actual failure
   surfaces for missing credentials, unavailable models, and launch failures.

The launch wizard currently requires a model and a valid source: repository
plus branch for Git, a path for a local mount, or the blank source choice.
Resource validation and Forge target matching can also block launch. These
conditions belong in the tutorial's troubleshooting section. A blank source
removes repository authentication from the first exercise, but its behavior
must be verified on the selected backend before using it in the tutorial.

## Proposed documentation structure

- **Start here:** what Niuu does, architecture image, and a direct link to the
  single first-session tutorial.
- **Tutorials:** first local session; first reusable team workflow; first
  resident. Each produces an observable result from a stated starting point.
- **How-to guides:** connect a repository; authenticate a runtime; configure
  a model provider; save a launch spec; connect service instances; use OpenShell;
  deploy on Kubernetes. Each has a concrete task and verification steps.
- **Concepts:** service composition, session versus workspace, Ravn and
  residents, rooms versus mesh versus A2A, Guild service groups, model routing.
- **Reference:** complete CLI options, configuration fields, API contracts,
  credential formats, and deployment settings. Explanations link here for detail.

## Rewrite order and acceptance criteria

1. Prove the local authentication and session path, then write the first
   tutorial from that run. Record failures as product issues instead of inventing
   missing setup steps or presenting an unverified path as complete.
2. Consolidate the duplicate onboarding pages and update navigation. Keep their
   existing URLs useful.
3. Write credentials and model-provider how-tos before adding more tutorials
   that depend on them.
4. Validate and document a reusable launch spec, then a team workflow and a
   resident, each with a small reproducible exercise.
5. Verify OpenShell and Kubernetes separately. Local success is not evidence
   for either deployment path.

A tutorial is ready when someone can complete it from its stated prerequisites
without searching source code or asking what a field means. Record the tested
version, date, commands, screenshots, and observed results. Run the strict site
build and link checks too, but do not treat them as runtime verification.

## Source anchors

- Startup lifecycle: `src/cli/commands/platform.py`,
  `src/cli/services/manager.py`, `src/cli/services/preflight.py`.
- Launch entry point: `web-next/packages/plugin-volundr/src/ui/SessionsPage.tsx`.
- Dialog labels and actions: `web-next/packages/plugin-volundr/src/ui/LaunchWizard.tsx`.
- Source inputs: `web-next/packages/plugin-volundr/src/ui/LaunchWizardSteps.tsx`.
- Runtime and access inputs: `web-next/packages/plugin-volundr/src/ui/LaunchWizardRuntimeStep.tsx`.
- Launch validation: `web-next/packages/plugin-volundr/src/ui/useLaunchWizard.ts`.

## Implementation status (2026-09-11)

The local tutorial, installation guide, project follow-on, and authentication
reference have been rewritten. Duplicate entry pages now point to the canonical
quick start. The release workflow includes a real binary bootstrap gate and an
optional authenticated file-creation gate. Source bootstrap and browser launch
were exercised; live file creation awaits valid Claude authentication. Published
macOS v1.3.0 fails PostgreSQL startup due to a missing dylib. See
`docs/site/operations/quickstart-verification.md` for the verification record.
Team/resident tutorials and OpenShell/Kubernetes runtime validation remain
separate follow-on work; this pass does not certify them.

## Full-site rewrite

The public landing page, navigation, concepts, service guides, operations,
reference introductions, and troubleshooting have now been rewritten. Generated
CLI pages remain derived from the implementation. The service smoke test also
executes the new local HTTP examples and verifies real Mímir ingestion/readback.
Internal cluster inventories and deployment-specific credential examples have
been removed from the public OpenShell guide. Live Ting execution, residents,
OpenShell, and cluster rollout still require their configured environments; the
rewritten pages do not claim those paths have passed.

## Packaging and repository follow-up

PostgreSQL native-library inclusion and macOS relocation now have an explicit
Nuitka configuration and build hook, validated with a compiled one-file database
probe. The published v1.3.0 artifact remains unchanged. The repository guide now
covers Git provider instances, organization/group discovery, token environment
configuration, persistent YAML, and the difference between catalog and clone
access. Authenticated GitHub discovery returned the Niuu repository successfully.
