# Initial-review evidence — 14 September 2026

This directory records a bounded real native-child demonstration and offline
contract checks. It is not a production implementation, live queue test, restart
test, release acceptance, or simulated demonstration presented as real work.

## Identity and runtime

- Reviewed source: `21bb0de6ec0ec467865382dd99640cf37b6cffdb`.
- Worktree: `/home/thor/repos/worktrees/niuu-codex-subagents-20260914`.
- Branch: `xteo/codex-subagents-workflow-20260914`.
- Parent Forge: `thor:0a713405-79b2-566c-a953-802e3a080c49`.
- Parent native: `01a09fd1-38e6-7b13-84c0-bbf0df249a47`.
- Coordinator recipient: `thor:8f20102d-6da7-58aa-98c2-e0bce2deab97`.

`runtime.json` records the read-only ancestor executable inspection and offline
version/schema generation. The running process used App Server, not a shell CLI
version guess. Source resolver: `src/skuld/transports/codex.py:26,66–89` prefers
explicit environment paths before PATH/bundle; launch is `codex_ws.py:529–569`.
No effective-runtime claim about Spark is inferred from this Thor observation.

`native-identities.json` contains selected fields from the parent's and the two
authorized children's own open rollout files, not unrelated conversations. Full
history forks can contain a later inherited parent `session_meta`; use the child's
own first matching identity record, not the last parent-looking record. Each
child's latest execution context independently confirms Astra/xhigh. Filesystem
read-only was an instruction constraint, not an enforced child sandbox.

## Actual demonstration

| Canonical tool identity | Native thread | Objective | Outcome |
|---|---|---|---|
| `/root/app_server_protocol` | `01a09fd1-c5c9-7973-8202-06e43bc1857f` | Official protocol + effective schema inventory for children/plans/goals/queue | Completed, substantive cited result; parent checked fresh queue/identity/status types and official sources |
| `/root/niuu_projection` | `01a09fd1-f13b-73d3-87d8-366f5b4eedf5` | Fixed-base NIUU normalization, persistence, ingress and presentation inventory | Completed, substantive code/line result; parent opened cited paths and ran offline regression groups |

Both were launched once through actual `collaboration.spawn_agent`; neither could
fan out under its assignment. The parent continued runtime/schema/workflow work
in parallel, received milestone messages, used `send_message` to narrow final
checks, inspected `list_agents` showing both running, and invoked
`wait_agent(timeout_ms=180000)` (returned without timeout). Final `list_agents`
reported **both completed** with their actual result bodies. No follow-up new task,
relaunch, archive or external Forge session was created.

Independent own-session HTTP reads, not only model tool acknowledgements:

- [Both running](live-own-agents.json):
  `GET /s/0a713405-79b2-566c-a953-802e3a080c49/api/agents`, through Thor.
- [Both done](live-own-agents-finished.json): same endpoint with
  `?include_finished=true`; protocol child ended `12:13:05 UTC`, NIUU child
  `12:13:37 UTC`, correct parent IDs retained.
- [Plan read](live-own-plan.json): empty. No native plan tool was exposed/called in
  this run; absence here does not invalidate schema/adapter plan capability.

The live API's `done` rows contain no full evidence result. Native child final
messages and parent inspection supply the acceptance evidence; a consumer must
not treat those rows alone as validated completion. The child protocol report
initially qualified its UUID as unverified; the parent independently established
the UUIDs through the allowed own-tree rollout and API reads above.

**Not exercised:** child failure/stall/cancel, parent interruption/restart, live
queue mutation/reorder/durability, extra children, device/visual tests, runtime
deployment. No active child work remained at the final check. Completed native
thread history is retained, not deleted.

## Offline tests actually executed

Existing interpreter only; `PYTHONPATH` explicitly selects this worktree's source.
Tests use mocks/local temporary files; no provider sessions, tmux tests or real
database connections were run. These are focused checks, **not a full coverage
gate or permission to merge/deploy**. No production code was changed.

```sh
PYTHONPATH="$PWD/src" /home/thor/repos/niuu/.venv/bin/python -m pytest \
  tests/test_skuld/test_codex_protocol_fidelity.py \
  tests/test_skuld/test_codex_delivery_regressions.py \
  tests/test_skuld/test_codex_text_identity.py \
  tests/test_skuld/test_forge_plan_agents.py \
  -m 'not integration and not tmux' -q -o addopts=''
```

[Result](focused-tests.log): **120 passed, 6 deselected**, 1.30 seconds, no warnings
reported. The protocol suite explicitly forbids actual subprocess creation.

```sh
PYTHONPATH="$PWD/src" /home/thor/repos/niuu/.venv/bin/python -m pytest \
  tests/test_skuld/test_forge_agentic_capture.py \
  tests/test_skuld/test_live_steering_routing.py \
  tests/test_skuld/test_codex_runtime_alignment.py \
  tests/test_skuld/test_forge_codex_faults.py \
  tests/test_adapters/test_pg_message_delivery.py \
  -m 'not integration and not tmux' -q -o addopts=''
```

[Result](ownership-tests.log): **76 passed**, 0.69 seconds, no warnings reported.
The PostgreSQL-adapter tests use mocked pool/connection objects, not a database.
These test current behavior; they do not prove newly proposed queue or recovery
semantics. Not every passing existing expectation is sufficient product behavior
(e.g. conflating disconnect with failure still needs the report's later contract).

## Schema and official sources

`protocol-extract.json` is an exact, selected extract from two fresh offline schema
generations of the running 0.154.0 executable. Full bundle/ClientRequest hashes and
generation commands are embedded. Original complete bundles remain in the private
worktree `.local/audit/schema-{stable,experimental}`. Included definitions may
reference omitted types: this is research evidence, not a replacement validator.

Parent verified experimental queue methods are absent in stable ClientRequest;
the stable notification still contains queue/changed. Earlier repository inventory
`docs/forge/codex-protocol-inventory-0.154.0.json:99–129` independently agrees, but
was not substituted for current effective-binary verification.

Official pages fetched on 14 September 2026:

- [App Server](https://learn.chatgpt.com/docs/app-server)
- [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Goals cookbook](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)

The detailed capability conclusions primarily derive from the local schema and
fixed source; documentation is not treated as proof of every installed behavior.

## Coordination and notifications

Request JSON and stable UUID were saved **before** each Forge receipt post.
Sender is the own Forge reference above, recipient is the coordinator, host alias
`thor` rather than a routing-registry UUID. Readiness and capability milestones
were posted as typed `progress` receipts, not injected as human-authored chat.
Final initial-review handoff follows report validation/push.

Human readiness/capability updates used the existing local OpenClaw route. Bodies,
private routing metadata and returned message IDs remain in `.local/audit/`;
route identifiers/auth are not copied into Git. No new hourly job was created.
Transport delivery is not task acceptance or proof the user read the message.

The project coordinator should curate accepted findings into the Lexi meta-repo.
This runner did not edit that shared checkout or another implementation worktree.
Session remains open for direct user iteration; no unattended scheduling is claimed.
