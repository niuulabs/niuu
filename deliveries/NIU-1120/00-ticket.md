# NIU-1120 — Flock sessions provision but never execute their workflow

- Tracker: https://linear.app/niuu/issue/NIU-1120/flock-sessions-provision-but-never-execute-their-workflow-idle-drive
- Status at start: Canceled; updated to In Progress for this revision pass via `./.skuld-tools/bin/tracker_issue update-status`.

## Problem

`ravn_flock` sessions launched by the Tool & Skill Builder workflow provision all containers, but persona drive loops remain idle and the workflow never emits the canonical `learned_tool.json` artifact. The commissioning A2A task remains `SUBMITTED`.

## Acceptance focus

Ensure workflow-backed flock sessions do not lose their startup trigger before Ravn persona sidecars are actually ready to consume mesh events.
