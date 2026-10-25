# NIU-1111 — A2A: expose campaign artifacts on tasks

- Delivery slug: `a2a-0f268aeb48a64ea3a0f2aa601051282b`
- Source branch: `regin`
- Required PR base and merge target: `regin`
- Working branch: `feat/niu-1111-a2a-task-artifacts`

## Tracker

The ticket was resolved and confirmed In Progress using the repository tracker shim:

```sh
./.skuld-tools/bin/tracker_issue search "NIU-1111"
./.skuld-tools/bin/tracker_issue get "f8e2f4ca-a618-4cae-bf7c-e7e69c9afcd0"
./.skuld-tools/bin/tracker_issue update-status "f8e2f4ca-a618-4cae-bf7c-e7e69c9afcd0" "In Progress"
```

## Acceptance

Completed A2A workflow tasks expose campaign artifacts, with one A2A Artifact and one Part per output. Text at or below the configured `a2a.inline_artifact_max_chars` limit is returned inline; larger output is represented by the existing authenticated campaign-artifact URL. Completed tool-builder tasks must include `learned_tool.json`; tests must use the a2a-sdk for inline and URL parts and cover the size boundary.

NIU-1110 is already an ancestor of `regin` (`dbb0ccf2`), so no `dev` into `regin` prerequisite merge was necessary.
