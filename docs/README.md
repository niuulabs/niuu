# Repository docs

The published documentation (<https://docs.niuu.cloud/>) is built from
[`site/`](site/) by `mkdocs.yml`. Everything else here is working material for
people changing the platform. New documents go in one of these folders, not
next to this file.

| Folder | What belongs there |
|---|---|
| [`site/`](site/) | The published MkDocs site. Links out of it use `blob/dev` URLs — relative links to the folders below fail the strict build. |
| [`architecture/`](architecture/) | Current-state descriptions: service boundaries, collaboration, migration playbook. |
| [`design/`](design/) | Feature designs with their research, vision and validation. |
| [`plans/`](plans/) | Implementation plans. |
| [`forge/`](forge/) | Forge and Skuld session work: designs, procedures, client handoffs, the [session orchestrator guide](forge/openclaw-session-orchestrator-guide.md). |
| [`ravn/`](ravn/) | Ravn and resident design, persona template, quickstarts. |
| [`operator/`](operator/), [`operations/`](operations/) | Running the platform: feature guides and credential/bootstrap procedures. |
| [`testing/`](testing/) | Acceptance procedures and test plans. |
| [`mockups/`](mockups/), [`demo/`](demo/) | UI mockups, demo material. |
| [`archive/`](archive/) | Superseded material kept for reference, including the legacy site. |

Dated run reports, review write-ups and evidence dumps do not belong in the tree:
put the finding in the pull request or the ticket, and keep only what someone
will act on later (a procedure, a design, a plan).
