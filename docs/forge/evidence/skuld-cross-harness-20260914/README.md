# Cross-harness research evidence

## Scope and provenance

September 14, 2026. Documentation/research only, source base
`21bb0de6ec0ec467865382dd99640cf37b6cffdb`, isolated branch
`xteo/codex-subagents-workflow-20260914`.

Runner `thor:0a713405-79b2-566c-a953-802e3a080c49`; supervisor
`thor:8f20102d-6da7-58aa-98c2-e0bce2deab97`.
No software installed, settings/auth changed, service restarted, production code
edited, foreign sessions inspected, or live provider acceptance tests run.

## Native child tracks

Only the two earlier native children were reused with follow-up tasks; no new
Forge sessions or child fanout. Both initial comparison follow-ups completed and
returned explicit source/version limits. Parent inspected the key claimed code
paths and fetched primary documentation and pinned public source independently.

| Child | Native thread | Comparison objective | Result inspected |
|---|---|---|---|
| `app_server_protocol` / Laplace | `01a09fd1-c5c9-7973-8202-06e43bc1857f` | Claude/tmux agents, teams and tasks | Current tools/model availability, hooks identity mismatch, team and queue semantics |
| `niuu_projection` / Gibbs | `01a09fd1-f13b-73d3-87d8-366f5b4eedf5` | Grok/Muse native protocol vs adapters | Pinned child controls, Muse queue durability claim, projection and resume gaps |

The parent native thread is `01a09fd1-38e6-7b13-84c0-bbf0df249a47`.
Earlier read-only native identity evidence is preserved in
[native read results](../codex-native-agent-ui-20260914/native-read-results.json).
Repeated SubagentStart/tool calls are attempts on reusable agents, not automatically
new agent identities. Both second-phase follow-ups completed and their results were
inspected: Laplace supplied official long-session examples and pinned NTM controller
evidence; Gibbs supplied public Codex policy/message/reload and cross-task sources.
Parent independently checked the selector, message dispatch, reload, NTM controller
and restore code, and retrieved the official cross-task PR descriptions. No native
children remain doing this research; completion callbacks are recorded in Forge's
session history. No additional child agents were launched.

## Tests actually executed

Initial system-Python collection failed on missing `asyncpg`; no tests executed in
that attempt. No dependency installation attempted. Then used the existing project
interpreter with imports selecting this worktree and bytecode writes disabled:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" \
 /home/thor/repos/niuu/.venv/bin/python -m pytest -q -o addopts='' \
 -m 'not integration and not tmux' \
 tests/test_skuld/test_pi_transport.py \
 tests/test_skuld/test_grok_transport.py \
 tests/test_skuld/test_muse_transport.py
```

Result: **91 passed in 2.06 seconds**, no warnings reported.
[Captured output](adapter-tests.log). Tests use mocked native processes/streams;
no live Grok/Muse/Pi execution, tmux or database acceptance was performed.
These passing tests do not prove the proposed API or every desirable invariant;
the Muse suite even contains a test expecting fresh-start fallback after failed
resume, which the report explicitly rejects as a continuity guarantee.

## Source capture

[Source manifest](source-manifest.json) records URL, retrieval time, size and SHA256
for selected primary-source snapshots. Full retrieved pages are private research
cache, not redistributed documentation. The report links to vendor originals.
Pinned Grok and Muse public commits are recorded in the report; mutable official
docs and Pi main-branch examples are dated observations, not release pins.

Important independent check: Claude teams documentation says the lead session
approves an arriving teammate plan without lead review; this was verified in the
official Markdown source, section “Have teammates plan before implementing.”
It is not a verified property of an existing live Claude session.

Effective runtime verification is strongest for this runner's Codex 0.154.0.
Claude PATH version is child-reported and not a live-launcher qualification.
Other current installed effective runtimes are explicitly unverified.

The effective schema's ignored `multiAgentMode` field was an important correction
to the earlier audit. [Extracted mode contract](codex-mode-contract.json) preserves
the field-level deprecation alongside the retained enum. The matching public source
was checked at `rust-v0.154.0`; tag match is not binary identity or proof of effective
private model-catalog overrides.

[Machine-readable comparison](capability-matrix.json) is a research classification,
not a claimed current Skuld endpoint response. [Document checks](document-checks.json)
record JSON/link/schema consistency, not native runtime acceptance.

Document validation checked Markdown artifacts and local links, six capability
rows, 20 downloaded-source hashes and the field-level mode deprecation, with no
errors; exact counts/timestamp are in the JSON record. The superseded first audit's
policy table is corrected too. Final Git checks and secret scan apply only to this
runner's branch. Completion delivery evidence is private under
`.local/audit/cross-harness-final-{receipt,notification}-response.json`; the receipt
identifier matched and notification had `dryRun:false` with a returned message ID.
Delivery is not coordinator acceptance or authority to implement the proposal.

## Remaining qualification

Real owned acceptance sessions would be required later for control/cancel timing,
native queue crash recovery, enabled plugins/features, budget enforcement, replay
and cross-client UI parity. No such tests are implicitly authorized by this report.
Document/schema checks are tracked separately from native/provider acceptance.
