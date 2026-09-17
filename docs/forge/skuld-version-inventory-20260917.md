# Skuld versions across Forge hosts

**Observed September 17, 2026, 19:13 UTC.** Read-only health requests; no session was stopped, resumed or updated.

## Result

- 664 session records returned by the four-host Forge inventory.
- All 664 session health routes checked: **19 verified live runtimes**, **645 returned 404**.
- **None of the 19 live runtimes matches the updated Thor release source.** Existing sessions intentionally retained their loaded engines during the API-only upgrade.
- The 645 other records are 634 archived, 10 stopped and 1 failed. Their current version is N/A, not guessed from the server or an old Git checkout.
- “Latest” below means the specific validated release now deployed on Thor, not a claim about every development branch. Seven runtimes report modified startup source; their revision alone does not identify their exact code.

## Server baseline

| Host | Server revision | Source clean? | Matches latest Thor release? |
| --- | --- | --- | --- |
| Thor | 45ea08f6 | Yes | Yes |
| Build | f44f62d5 | No — modified | No |
| Build Bro | c6d80980 | No — modified | No |
| Spark | 650088f3 | Yes | No |

Thor target build: forge-codex-ready-20260917-v1. Revision: 45ea08f69bca5fb8c63ed2ef4d98bcf707aa2029. Source digest: 1f1156a7d0a9063ffb97e36678d410f58ace879851b9c3584e03296defba4c1c. Documentation-only commits after that revision do not change its runtime source.

## All 19 reachable session runtimes

Stored lifecycle status is not a live-turn/idle verdict. Eight retained Thor gateways have stopped/archived records; this inventory does not authorize cleanup or restarting them.

| Host | Session | Stored status | Loaded Skuld revision | Matches Thor release? | Matches its own host source? |
| --- | --- | --- | --- | --- | --- |
| Build | build-storage-spike | running | f44f62d5 (modified) | No | Yes — modified |
| Build | horde-build | running | f44f62d5 (modified) | No | Yes — modified |
| Build Bro | horde | running | c6d80980 (modified) | No | Yes — modified |
| Build Bro | horde-brep | running | c6d80980 (modified) | No | Yes — modified |
| Build Bro | horde-build-bro | running | c6d80980 (modified) | No | Yes — modified |
| Build Bro | kit-coordinator | running | c6d80980 (modified) | No | Yes — modified |
| Thor | lexi-coordinator | running | 673b5000 (modified) | No | No |
| Thor | lexi-forge-niuu-management | running | c6d80980 | No | No |
| Thor | lexi-ios-chat-voice-ux-improvement | running | c6d80980 | No | No |
| Thor | lexi-macos-interface-parity | running | c6d80980 | No | No |
| Thor | niuu-ux-improvements | running | c6d80980 | No | No |
| Thor | lexi-codex-subagents-workflow | stopped | c6d80980 | No | No |
| Thor | lexi-web-chat-restoration | archived | 49ed405f | No | No |
| Thor | physics-architecture-review | archived | c6d80980 | No | No |
| Thor | physics-coordination | stopped | c6d80980 | No | No |
| Thor | physics-delivery-review | archived | c6d80980 | No | No |
| Thor | physics-gpt-live-upgrade | archived | c6d80980 | No | No |
| Thor | physics-pmt-capacitance | archived | c6d80980 | No | No |
| Thor | physics-pmt-magnetic-fields | archived | c6d80980 | No | No |

No reachable Skuld runtime was found among Spark’s 15 session records. The Spark server is still its older timeline-v4 release.

## Complete list and method

[All 664 records, Markdown](/home/thor/repos/worktrees/niuu-forge-runtime-management-20260913/.local/runtime-version-inventory-20260917/all-sessions.md)
[All 664 records, CSV](/home/thor/repos/worktrees/niuu-forge-runtime-management-20260913/.local/runtime-version-inventory-20260917/all-sessions.csv)

Raw health evidence is saved privately in /home/thor/repos/worktrees/niuu-forge-runtime-management-20260913/.local/runtime-version-inventory-20260917/inventory.json. Session identity was checked against the requested UUID. Host routes came from the visible registry; health probes did not forward credentials, connect WebSockets, read conversation histories or trigger provider work. A 404 establishes that this route has no reachable gateway; it is not an OS process scan or a historical-build claim.

[Safe per-session upgrade procedure](session-runtime-upgrades-20260917.md). Version mismatch alone is not permission to migrate a session; idle-only unattended restart remains unsafe without an ingress fence.
