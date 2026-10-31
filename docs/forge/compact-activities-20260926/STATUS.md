# Compact activity authoring — local Forge patch

September 26, 2026. User-authorized follow-up to accepted Lexi iOS Activities2289.
This is a source/test/build result, **not a deployed Forge update**.

- Both transcript and feed-only MCP `notify` descriptions use shared guidance:
  an outcome/action title (aim72 characters), optional one-sentence body (aim160),
  no repeated title, report, checklist or test/log inventory. Details belong in the
  conversation or linked evidence. Forge supplies project/session/host/model/time.
- Both generated Claude and Codex `forge-notify` skills contain that same guidance.
  Existing sessions' loaded tool/skill metadata is not hot-refreshed by this commit.
- Existing API bounds, records, delivery semantics and permissions are unchanged.
  These are authoring targets, not new truncation/rejection rules.
-144 focused MCP/adapter/broker/injection tests pass,96.44% coverage across the touched
  guidance/tool/skill modules;85% gate retained. Ruff check/format pass.
- Wheel builds: `.local/dist/volundr-0.1.0-py3-none-any.whl`, SHA256
  `4d7908f6e3bed37d16890d76e0b1eab4e12c0243edcd6f737c3028ace3a94e43`.

[Tests](TESTS.txt) · [Build](BUILD.txt).

Worktree `/home/thor/repos/worktrees/niuu-compact-activity-guidance-20260926`, branch
`xteo/compact-activity-guidance-20260926`, based on shipped Projects `bf9fd37e` which
includes deployed notification anchors `5abeb85e`. No other runner's files changed,
no service restart, server writes, provider calls or live-session injection.

Roll this wording into the next authorized Forge/Skuld release; verify tool-list
metadata and a newly materialized skill there. Client compactness does not depend
on deployment: historical long cards are already compacted by the iOS change.
