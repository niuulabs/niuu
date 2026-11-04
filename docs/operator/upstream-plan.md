# Upstream plan: the xteo fork's delta as PRs against niuulabs `dev` (LEX-1705)

The xteo fork (`xteo/niuu`, branch `lexi/dev-api-integration`) carries 83 commits that are
not on `niuulabs/niuu` `dev`. This plan splits them into 19 pull requests, each on its own
branch on the fork, rebased on `dev` as of **24012e704** (2026-10-30). **Nothing has been
pushed to niuulabs.** Damien decides when the PRs are opened.

## How the branches are built

- **One rebase, cut at feature boundaries.** The fork's delta was rebased onto `dev` once,
  in feature order, as `lexi/upstream-stack`. Each branch `upstream/NN-name` is the stack up
  to that feature's last commit. A PR therefore targets `dev` once the PRs before it have
  merged; until then it targets the previous branch (a stacked PR).
- **Independent PRs.** Four branches also apply on `dev` alone, with no earlier PR:
  **01, 05, 08 and 15**. They can be opened and merged in any order. For 05, 08 and 15,
  open the PR from `upstream/NN-…-on-dev`: the same commits on `dev` directly. 01 is
  already on `dev`. Once one of them merges, git drops its commits from the stacked
  branches as already applied when they are rebased. The others conflict
  with `dev` without their predecessors (the files are listed per PR below), so they
  merge in order.
- **The ticket's order and this one.** The ticket listed work facts first. Five smaller
  features come before it here, because later PRs build on them: the attachments index,
  session descriptions, notification images, the conversation tail and the Skuld auth
  fixes. The ticket's numbering is given in brackets in the table.
- **Commits keep their history.** Each feature keeps its original commits (messages,
  review fixes), so a reviewer sees how it was built. Commits that only renumber a
  migration are named "number the … migration after dev's 000089".

## Merge order

| # | Branch | Ticket item | Commits | Migration | Alone on `dev`? |
|---|---|---|---|---|---|
| 01 | `upstream/01-attachments-index` | — | 2 | — | yes |
| 02 | `upstream/02-session-descriptions` | — | 3 | 000090 | after 01 |
| 03 | `upstream/03-notification-images` | — | 6 | 000091 | after 02 |
| 04 | `upstream/04-conversation-tail` | — | 2 | — | after 03 |
| 05 | `upstream/05-skuld-auth-isolation` | — | 2 | — | yes |
| 06 | `upstream/06-work-facts-usage` | (1) | 5 | 000092, 000093 | after 05 |
| 07 | `upstream/07-presented-files-artifacts` | (2) | 19 | 000094 | after 06 |
| 08 | `upstream/08-web-themes` | — | 1 | — | yes |
| 09 | `upstream/09-asks-question-standard` | (3) LEX-1486, 1671, 1502 | 7 | — | after 08 |
| 10 | `upstream/10-model-in-effect` | (4) LEX-1493 | 1 | — | after 09 |
| 11 | `upstream/11-speak-intent` | — LEX-1536 | 2 | 000095 | after 10 |
| 12 | `upstream/12-mcp-grants` | (5) LEX-1506 | 1 | — | after 11 |
| 13 | `upstream/13-preview-size` | (12) LEX-1655 | 1 | — | after 12 |
| 14 | `upstream/14-voice-reads` | (7) LEX-1677 | 1 | — | after 13 |
| 15 | `upstream/15-broker-liveness` | (8) LEX-1670, 1699 | 4 | — | yes |
| 16 | `upstream/16-notification-resolved` | (6) LEX-1442 | 3 | 000096 | after 15 |
| 17 | `upstream/17-presented-files-quota` | (9) LEX-1514, 1515 | 3 | — | after 16 |
| 18 | `upstream/18-worked-time` | (10) LEX-1465 | 1 | — | after 17 |
| 19 | `upstream/19-push-gateway` | (11) LEX-1353 | 4 | 000097 | after 18 |

"After NN" means the branch is stacked on NN. A stacked PR's real dependency is often
narrower (see "Conflicts with `dev`" per PR), but the order above is the one that was
built and tested.

## Migrations and lineage aliases

`dev` ends at `000089_odin_review_relay`, which the fork also used: its
`000089_session_descriptions`. Every fork migration therefore takes a new number, in
merge order. **The SQL is byte-identical**; only the filename changes. Each new name
adopts the fork's earlier names through `migrations/lineage-aliases.json`, so Thor and
mini-m6, which applied them under the fork's names, are not migrated twice when they run
`dev`.

| New name (`dev`) | PR | Fork name(s) adopted | Effect |
|---|---|---|---|
| `000090_session_descriptions` | 02 | `000087_…` (Thor, before the B0 rebase), `000089_…` | four nullable `sessions` columns |
| `000091_forge_notification_images` | 03 | `000072_…` (Thor), `000090_…` | table `forge_notification_images` |
| `000092_session_work_facts` | 06 | `000091_…` | work-fact columns on `sessions` |
| `000093_session_usage_status` | 06 | `000092_…` | usage-status columns on `sessions` |
| `000094_forge_artifact_publications` | 07 | `000093_…` | three artifact tables |
| `000095_forge_notification_speak` | 11 | `000094_…` | `speak_text`, `speak_priority` on `forge_notifications` |
| `000096_forge_notification_resolved` | 16 | `000095_…` | resolved state on `forge_notifications` |
| `000097_push_gateway` | 19 | `000073_…` (fork branch), `000096_…` (fork release) | `device_tokens` kinds; two gateway tables |

**If the merge order changes,** the numbers must follow it: whichever of these merges
first takes 000090, and so on. The rename and its alias entry are one small commit per
migration, so renumbering is mechanical. A PR merged out of order needs its number
changed, and its alias kept, before it lands.

Every migration is idempotent (`IF NOT EXISTS`), and each has a down migration.

## The PRs

Each section gives what the PR does, its tests, the conflicts it had with `dev`'s head,
and what Jozef must configure. "Off by default" means a deployment changes nothing until
it sets the option.

### 01 Attachments index — independent

`GET /api/v1/forge/sessions/{id}/attachments[?kind=document|image]`: every
`present_file` / `SendUserFile` delivery and every image tool result of a session in one
call (LEX-1259). It is folded from the durable event log incrementally, in bounded
memory, and cached on the log's head. The Guild forwards it to the owning node, and the
feature flags advertise `attachments_index`.
- **Tests:** `tests/test_volundr/test_attachments_index.py`.
- **Conflicts:** none.
- **Configure:** nothing.

### 02 Session descriptions

Agents keep a one-line and a long description of what their session is doing
(`describe_session` MCP tool, `forge-describe` CLI, `PUT …/description`). It is shown
in the session list.
- **Tests:** the migration, Postgres round-trip, REST, MCP tool and an end-to-end test.
  `docs/forge-session-descriptions.md` is the contract.
- **Conflicts with `dev`:** `dev` added the Ravn flock tools (`flock_members`,
  `flock_delegate`, `flock_outcomes`) in the same places: `niuu/forge_mcp/tools.py`,
  `skuld/forge_mcp/integration.py` and `skuld/transports/session_tools.py`. Both are
  kept, and `capability_instructions` takes `flock_broker` and adds the description
  instruction.
- **Configure:** nothing.

### 03 Notification images

`notify` accepts an image (a session file or an https URL). Forge copies it once in the
background, makes a thumbnail, and serves both; the Guild routes the image URL to the
owning node.
- **Tests:** config, migration and alias, Postgres, fetcher, worker, REST and facade.
- **Conflicts with `dev`:** `dev`'s ODIN review relay (`config.py`,
  `domain/notifications.py`, `main.py`, its migration test and config tests): both are
  kept. `dev`'s ODIN relay tests build `NotificationService` without the two image ports
  this PR makes required; they are given them.
- **Configure:** `notifications.images` has defaults. `allow_private_networks` stays off
  unless image URLs point at tailnet or private hosts.

### 04 Conversation tail

`GET …/conversation?tail=N` returns the newest N turns and reads only the end of the
source (LEX-1345): the broker's protocol-2 recent page while the session runs, or the
durable log read backwards in growing windows once it has stopped. Without `tail`,
responses are unchanged.
- **Conflicts:** `session_archive.py`; it needs 03.
- **Configure:** `conversation_tail_initial_frames` and `conversation_tail_growth` have
  defaults.

### 05 Skuld auth isolation — independent

Child processes of the broker no longer inherit Forge's capture signing secret. An
explicitly configured private Claude OAuth file is read only for direct Claude
inference: conflicting auth is refused, and the file stays out of other engines.
- **Conflicts:** none.
- **Configure:** nothing.

### 06 Work facts and usage status (ticket item 1)

Fixes the stale `active` state. Each session row carries `last_work_at`, `tool_open` and
`open_controls`, and the native usage (tokens, context window) appears in the status and
live context.
- **Tests:** both migrations, the response models, the Postgres integration lane.
- **Conflicts with `dev` alone:** `forge_mcp/tools.py`, `rest.py`, `broadcaster.py`,
  `postgres.py` and `models.py`, through 02 to 04.
- **Configure:** `stale_output_seconds` and `session_usage.model_windows` have defaults.

### 07 Durable presented files and artifact publications (ticket item 2)

Forge keeps its own copy of every presented file, served on a tailnet URL so it opens
after the session stops. Every artifact publish, `present-file`, notify link and
`report_artifact` is recorded, and one notice is sent per burst.
- **Tests:** 29 test files, including the replay fixtures and the Postgres lane.
- **Conflicts with `dev`:** `report_artifact` sits next to `dev`'s flock port and host
  classes in `forge_mcp/ports.py` and `skuld/forge_mcp/host.py`, and was placed in its
  own class.
- **Configure:** `presented_files` is off by default. Enabling it needs a persistent
  single-writer directory (`store.root`, for example a PVC) and a tailnet
  `public_base_url`. `artifacts` and `host_identity` have defaults. The chart renders all
  three.

### 08 Web themes — independent

A midnight-blue default theme, plus deep-blue and dark-blue themes.
- **Conflicts:** none (the fork's `CONSOLIDATION.md` record was left out).
- **Configure:** nothing.

### 09 Structured asks and the question standard (ticket item 3: LEX-1486, 1671, 1502)

`notify` carries question signals (`question`, `about_question`) and a structured `ask`
(choices, multi-select, option descriptions, a default). Forge-raised `attention` items
name the runtime request they stand for. The question standard checks agent questions,
and the web question form shows multi-select, option descriptions and refusal text.
- **Conflicts with `dev`:** `dev`'s ODIN fields in `rest_notifications.py` and in
  `Notification.wire()` and its properties are kept next to the ask fields.
- **Configure:** nothing.

### 10 Model in effect (ticket item 4, LEX-1493)

A session reports the model actually in effect, and Claude sessions offer their model
choices.
- **Conflicts with `dev` alone:** `tmux_interactive.py`, `rest.py` and `session_usage.py`,
  through 06.
- **Configure:** nothing.

### 11 Speak intent (LEX-1536)

A notification can carry `speak_text` and `speak_priority` for a voice client.
- **Configure:** nothing.

### 12 Forge MCP grants route and role defaults (ticket item 5, LEX-1506)

`GET`/`PUT /api/v1/forge/sessions/{id}/forge-mcp` lets an operator read and replace a
session's Forge MCP grants. Each change is audited, and the next start mints the new
grants. `forge_mcp.role_grants` grants by project role.
- **Configure:** `forge_mcp.role_grants` defaults to coordinator → lifecycle + message;
  other roles get none.

### 13 Preview size hint (ticket item 12, LEX-1655)

`max_edge` on the tool-result preview.
- **Configure:** `tool_result_preview` (`default_edge`, `min_edge`, `max_edge`) has
  defaults.

### 14 Voice reads (ticket item 7, LEX-1677)

`conversation?detail=voice`, a session-list `limit` and a summary list, and the flags
`capabilities.voice_conversation` and `capabilities.summary_sessions`.
- **Configure:** nothing.

### 15 Broker liveness (ticket item 8, LEX-1670, 1699) — independent

- A capped tmux turn keeps the agent's reply.
- A broker never adopts a tmux session stamped by another broker.
- A dead Claude pane stops the session (`transport_stopped` and broker exit) instead of a
  running corpse.
- The socket sweep spares young and own sockets.
- **Tests:** transport, adoption guard, broker exit, real-tmux tests.
- **Configure:** `exit_when_transport_stops` (default true);
  `SKULD__TMUX_SOCKET_SWEEP_MIN_AGE_SECONDS` (default 600 s).

### 16 Notification resolved state (ticket item 6, LEX-1442)

Notifications can be resolved (`PUT …/resolve`, `notify resolves`, `?unresolved=true`),
and alarms whose session moved on are flagged `stale`.
- **Configure:** `notifications.stale_after_seconds` has a default.

### 17 Presented-files quota and Forge health (ticket item 9, LEX-1514, 1515)

A total quota for durable presented files, refused loudly and counted. Forge health
(with presented-file usage) is served through the Guild. The present-file docs say it
reads only the workspace and home.
- **Configure:** `presented_files.max_total_bytes` is **required** when presented files
  are enabled: the size the volume may hold.

### 18 Worked time (ticket item 10, LEX-1465)

`worked_seconds_24h` on every session row.
- **Configure:** nothing.

### 19 APNs push gateway and Live Activity relevance (ticket item 11, LEX-1353)

One node of a Guild reads every node's notification feed and pushes to Apple devices:
- alerts;
- Live Activity updates and push-to-start;
- widget reloads;
- background pushes.

It has its own alert outbox and throttles, plus `GET /push/status` and
`POST /push/test`. Live Activity pushes carry `aps.relevance-score`
(`push.gateway.live_activity.relevance_score`, default 10), so a live call (100, set by
iOS) wins the Dynamic Island.
- **Conflicts with `dev`, and the decision Jozef should check:** `dev`
  (c8f790320, 86cd870f4) made the Guild's device routes **fan out to every node**,
  because each node pushes alerts to its own devices. It also deduplicates a device's
  connections in the credential-store registry. This branch keeps both, and:
  - an `alert` registration fans out as `dev` does;
  - Live Activity, push-to-start and widget tokens serve only the gateway, so they
    register and unregister on the gateway's node alone. A node that keeps alert tokens
    only refuses those kinds (422), which would otherwise fail the whole fan-out;
  - the listing keeps one row per token **and kind**, and `DELETE ?kind=` is passed
    through.

  The alternative is a Guild-level setting naming the gateway node. In a Guild with a
  gateway, the other nodes keep `push.enabled` off, or an alert is sent twice.
- **Tests:** payloads, gateway service, composition, APNs client, device registry,
  Postgres outbox, the gateway against the real Guild facade, facade routing
  (`test_rest_volundr_push_gateway.py`), and the Postgres lane.
- **Configure:** off by default. To run it, set on one node:
  - `push.enabled: true`;
  - `push.device_registry: postgres` (or `auto` in mini mode);
  - `push.apns`: `team_id`, `key_id`, `bundle_id`, and `private_key_path` (the app's
    `.p8`);
  - `push.gateway`: `enabled: true` and `guild_url`.

  With the gateway on, the node gives no "Needs-input pushes" rule, and `push.adapter`
  must stay at its default.

## Follow-ups for Jozef (need his and Damien's sign-off)

- **Node credential for cross-node session relays (LEX-1710, option B).** The fork's
  relay of a session's message to its parent or project on another node carries no
  credential. It works where the owning node accepts an unauthenticated caller
  (mini-mode Guilds on a tailnet), and is refused clearly on a node that requires a
  login. To make it work on login-requiring clusters:
  - the home node signs the relayed request with a node key, reusing the Ed25519
    node-request signing of `niuu join` (today only heartbeat and leave);
  - member hosts join their Guild with `niuu join`;
  - the exception in `.claude/rules/architecture.md` is extended with the same
    enforcement points (FastAPI dependency, middleware exemption, chart Envoy bypass).

  That changes the authentication model, so it is Jozef's and Damien's decision. The
  relay itself (option A) joins the stack after branch 19 once it is upstream-ready.

## Left out

These fork commits are records of the fork's own operation, not product changes:

- the LEX-1483 consolidation and B1 lift records (`CONSOLIDATION.md`);
- the B3 promotion runbook and switch records;
- the per-promotion notes.

Seven research notes on native Codex and agent capabilities (`docs/forge/…`,
`docs/skuld/…`, 90dbf384f … 75b066263) can follow as a docs-only PR if Jozef wants them.

## Verification

Each branch tip was checked with the full backend suite (`-n 16`, with coverage),
comparing its failures with `dev`'s own. `dev` 24012e704 fails 17 tests on this host,
all box-specific (Docker, the install script, the release guard, Kubernetes log tooling).
The `-on-dev` rows are the independent PRs built directly on `dev`.

| Branch | Passed | Failed | Beyond `dev`'s set | Coverage |
|---|---|---|---|---|
| `upstream/01-attachments-index` | 29401 | 17 | none | 89% |
| `upstream/02-session-descriptions` | 29461 | 17 | none | 89% |
| `upstream/03-notification-images` | 29603 | 17 | none | 89% |
| `upstream/04-conversation-tail` | 29619 | 17 | none | 89% |
| `upstream/05-skuld-auth-isolation` | 29665 | 16 | none | 89% |
| `upstream/06-work-facts-usage` | 29856 | 16 | none | 89% |
| `upstream/07-presented-files-artifacts` | 30087 | 16 | none | 89% |
| `upstream/08-web-themes` | 30087 | 16 | none | 89% |
| `upstream/09-asks-question-standard` | 30234 | 16 | none | 89% |
| `upstream/10-model-in-effect` | 30261 | 16 | none | 89% |
| `upstream/11-speak-intent` | 30299 | 16 | none | 89% |
| `upstream/12-mcp-grants` | 30325 | 16 | none | 89% |
| `upstream/13-preview-size` | 30340 | 16 | none | 89% |
| `upstream/14-voice-reads` | 30359 | 16 | none | 89% |
| `upstream/15-broker-liveness` | 30375 | 16 | none | 89% |
| `upstream/16-notification-resolved` | 30385 | 16 | none | 89% |
| `upstream/17-presented-files-quota` | 30398 | 16 | none | 89% |
| `upstream/18-worked-time` | 30403 | 17 | 1: a Ting concurrency test, timing under load (passes 5 of 5 alone; the branch changes no Ting code) | 89% |
| `upstream/19-push-gateway` | 30749 | 16 | none | 90% |
| `upstream/05-skuld-auth-isolation-on-dev` | 29425 | 16 | none | 89% |
| `upstream/08-web-themes-on-dev` | 29380 | 16 | none | 89% |
| `upstream/15-broker-liveness-on-dev` | 29396 | 16 | none | 89% |

The web suite (`pnpm test`, coverage-gated) passes at 08 (7736 tests) and 09 (7741 tests), the two branches that change `web-next`.

## Adoption by the fork's hosts

**What happens when Thor or mini-m6 later runs `dev` with these PRs merged:** a dry run
answers this. The runner of branch 19 (all PRs) was run on read-only copies of both hosts'
schema and ledger (2026-10-30 21:25 UTC; scratch PostgreSQL 16 for Thor, 17 for the
mini). Results:

| | Thor (ledger 118) | mini-m6 (ledger 139, before promotion six) |
|---|---|---|
| executes | `000089_odin_review_relay` (`dev`'s own) | `000089_odin_review_relay`, `000097_push_gateway` |
| adopted by alias | 000090 to 000097, each from its fork name (000090 from Thor's `000087_…`, 000091 from `000072_…`) | 000090 to 000096 |
| second run | no-op | no-op |

The mini executes 000097 only because it had not taken promotion six (000096) yet; once
it has, 000097 is adopted there too. A migration is adopted only when the ledger row of
its fork name carries the same sha256, so a changed file fails loudly instead of being
skipped.

The database lane (`scripts/verify_forge.py database`, real PostgreSQL 16) passes on
branch 19: **166 passed**.

## Appendix: commits per branch

### upstream/01-attachments-index

- `aac60c38e` feat(forge): add a session attachments index route
- `0ab59ad80` perf(forge): fold the attachments index incrementally in bounded memory

### upstream/02-session-descriptions

- `e849d8cbf` feat(forge): add agent-maintained session descriptions
- `cd5446edf` test(forge): prove session descriptions end to end and document the contract
- `e9e3afe95` fix(volundr): number the session descriptions migration after dev's 000089

### upstream/03-notification-images

- `50fa79a9c` feat(forge): accept an image reference on notify
- `c2a3eedb3` refactor(volundr): share how Forge reaches a session broker
- `91cf1f3ff` feat(volundr): keep a durable copy of notification images
- `19de1ed2f` feat(niuu): route notification images through the Guild facade
- `7f098f7b3` docs(forge): document notification images
- `4f0b346bd` fix(volundr): number the notification images migration after dev's 000089

### upstream/04-conversation-tail

- `8ba38b7ba` feat(volundr): read only the newest turns with conversation ?tail=N
- `cc0e0a453` feat(volundr): let conversation tail override a protocol-2 page request

### upstream/05-skuld-auth-isolation

- `aae3e02da` fix(skuld): exclude Forge capture secret from child environments
- `192ff7f36` fix(skuld): isolate Claude subscription file authentication

### upstream/06-work-facts-usage

- `3e958164c` fix(forge): stop stale active state and serve work facts (G10, F12)
- `074974bfd` fix(forge): address review of the work-facts lift
- `47a01b1b8` feat(forge): project native session usage into status and live context
- `bddbba22a` test(forge): bring the database lane and history-import tests up to B1a
- `893e014d5` fix(volundr): number the work facts and usage status migrations after dev's 000089

### upstream/07-presented-files-artifacts

- `aefb15b08` feat(forge): keep durable presented files with tailnet URLs (F2)
- `98ee396ff` feat(forge): record artifact publications and announce each burst once
- `4b3329fdf` fix(forge): harden the durable presented-file store from review
- `eb57d5466` feat(forge): clean up presented files on delete and resume uploads after restart
- `b92b27210` fix(forge): close the review's races in presented-file cleanup and resume
- `dba61caf3` fix(forge): let the broker's durable upload reach Forge through the facade (F2)
- `ae14751e0` fix(forge): bound the facade's presented-file upload and validate its id
- `9a768b2b5` fix(forge): keep the presented-file sandbox through the public facade (judge R16-1)
- `8d0712dc7` fix(forge): the presented-file policy at the broker too (review of R16-1)
- `07df5a46d` test(forge): keep unknown titles unknown in artifact replay
- `a1bb18374` fix(forge): record a presented file's durable tailnet URL on its publication
- `60c4953a8` fix(forge): accept only tailnet hosts for durable presented-file URLs
- `96640c735` fix(forge): record artifact publications before acknowledging an append
- `3cf71edd3` fix(forge): make a burst settled at the emission cap loud
- `e0f1320b2` fix(forge): name the host once for publications and the status card
- `81ef5cb2a` feat(charts): render artifacts, presented files and host identity config
- `5591a4748` test(forge): a burst notice's live event carries the Guild instance
- `26feab94c` fix(skuld): make an exhausted durable-upload retry loud
- `ccbbc9be3` fix(volundr): number the artifact publications migration after dev's 000089

### upstream/08-web-themes

- `65e67c974` feat(web): midnight-blue default, deep-blue and dark-blue colour themes

### upstream/09-asks-question-standard

- `bbac3d8d2` feat(forge): add question signals to the notify tool (F11b)
- `b9852d861` feat(forge): carry structured question and action asks in notifications
- `9b0213366` fix(forge): retain notify in native Claude tool discovery
- `53c9d1447` feat(forge): name the runtime request on attention notifications
- `5a9b83234` test(forge): question signals survive Postgres storage and the outbox
- `c35800951` feat(niuu): the question standard on agent notifications (LEX-1671)
- `7c28b4b93` feat(web): restore multi-select, option descriptions and refusal text to the question form (LEX-1502)

### upstream/10-model-in-effect

- `0d8109687` feat(forge): report the model in effect and offer Claude model choices

### upstream/11-speak-intent

- `3173b10a0` feat(volundr): carry a speak intent on agent notifications (LEX-1536)
- `4dcb6be0f` fix(volundr): number the speak intent migration after dev's 000089

### upstream/12-mcp-grants

- `b40f4819d` feat(volundr): operator route for Forge MCP grants and grants by role (LEX-1506)

### upstream/13-preview-size

- `e56faf4c0` feat(volundr): max_edge size hint on the tool-result preview (LEX-1655)

### upstream/14-voice-reads

- `833e56775` feat(volundr): voice reads — detail=voice, session list limit and summary (LEX-1677)

### upstream/15-broker-liveness

- `416ae7005` fix(skuld): a capped tmux turn keeps the agent's reply (LEX-1670)
- `0eee5c05b` fix(skuld): never adopt a tmux session whose Claude reports to another broker (LEX-1670)
- `fa669efe9` fix(skuld): a dead Claude pane stops the session instead of a running corpse (LEX-1699)
- `68d004252` fix(skuld): the socket sweep spares a sibling's starting tmux server (LEX-1699)

### upstream/16-notification-resolved

- `7e87df528` feat(volundr): resolved state for notifications (LEX-1442)
- `2bf52e787` fix(volundr): number the resolved-state migration after dev's 000089
- `7929b1473` feat(volundr): stale flag for alarms whose session moved on (LEX-1442 item 4)

### upstream/17-presented-files-quota

- `32a0ca64d` feat(volundr): total quota for durable presented files (LEX-1514)
- `5fe297edb` fix(niuu): serve Forge health, with presented-file usage, through the Guild (LEX-1514)
- `e167dd5d4` docs(skuld): present-file reads only the workspace and home; say so and how (LEX-1515)

### upstream/18-worked-time

- `f74571e56` feat(volundr): worked_seconds_24h on every session row (LEX-1465)

### upstream/19-push-gateway

- `df20ca28f` feat(volundr): port the Guild APNs push gateway onto integration
- `9eee50f12` feat(volundr): give Live Activity pushes an APNs relevance score (LEX-1353)
- `908e1f970` test(volundr): fit the push gateway's Guild tests to integration's facade
- `a572222f7` fix(volundr): fit the push gateway to dev's device fan-out and number its migration

