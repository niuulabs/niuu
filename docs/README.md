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
| [`plans/`](plans/) | Implementation plans, plus the evidence files they cite. |
| [`forge/`](forge/) | Forge and Skuld session work: protocols, reviews, rollout evidence, the [session orchestrator guide](forge/openclaw-session-orchestrator-guide.md). |
| [`ravn/`](ravn/) | Ravn and resident design, persona template, quickstarts. |
| [`operator/`](operator/), [`operations/`](operations/) | Running the platform: feature guides and credential/bootstrap procedures. |
| [`testing/`](testing/), [`integration/`](integration/) | Acceptance procedures and dated test or integration reports. |
| [`mockups/`](mockups/), [`demo/`](demo/) | UI mockups, demo material. |
| [`archive/`](archive/) | Superseded material kept for reference, including the legacy site. |
