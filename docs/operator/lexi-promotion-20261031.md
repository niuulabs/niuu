# Promotions six to thirteen, and Thor's push gateway (2026-10-30/31)

This is the record of the promotions that took Thor and mini-m6 from five
(`e6257217d`) to thirteen (`6ff003fb9`), on 2026-10-30 and 2026-10-31. The runbook is
[lexi-promotion-20261026.md](lexi-promotion-20261026.md); the busy-lane rule it uses is
§1.3a. The push gateway is documented in [lexi-push-gateway.md](lexi-push-gateway.md).
The cutover artifacts stay with each rollout folder on Thor
(`~/.local/share/niuu/rollouts/forge-<name>/`). No secrets are recorded here.

## What each promotion carried

| # | Commit | Release (Thor / mini) | Carried |
|---|---|---|---|
| six | `8e43d0443` | `forge-gateway-8e43d044` / never switched | APNs push gateway port; LEX-1353 Live Activity relevance score; migration `000096_push_gateway` |
| seven | `58debfa58` | `forge-relay-58debfa5` / `forge-m6-relay-58debfa5` | LEX-1710 cross-node parent/project messages; Codex app-server liveness (LEX-1699); LEX-1706 busy-lane gate; `guild.url` on the mini |
| eight | `39ab05da0` | `forge-names-39ab05da` / never switched | malformed `instance_id` answers 400; the gate names each blocking process |
| nine | `c42970e6c` | `forge-attn-c42970e6` / `forge-m6-attn-c42970e6` | LEX-1186 part 4 (attention pushes marked for the app's notification extension, with `engine`); `send_message` with `instance_id` relays; push gateway operator doc |
| ten | `b25aec7f6` | `forge-guild-b25aec7f` / never switched | LEX-1738: a member's lane reaches any Guild session by id (relay through the Guild, owner named on a member read, Guild-scope listing includes other nodes, own node first); Spark's missing feed remembered (`feedless_nodes`) |
| eleven | `5aac51a5f` | `forge-env-5aac51a5` / `forge-m6-env-5aac51a5` | read-state skips feedless nodes (per-route memory); the release dist stager (`scripts/forge_release_dist.py`); push counts per APNs environment (package 2) |
| thirteen | `6ff003fb9` | `forge-fallback-6ff003fb` / `forge-m6-fallback-6ff003fb` | the `widget_fallback` token kind (stored, counted per environment, never pushed to); 12-hex SHA-256 token fingerprints on registration and prune log lines, the app's recipe |
| twelve | `ec03df0c3` | `forge-owners-ec03df0c` / `forge-m6-owners-ec03df0c` | a session read goes to the node remembered owning it (learned from Guild listings); `instance_name` in the MCP session summary; a parent's `guild_instance_id` beside its stored projects id; facades never ask each other in a loop (`X-Niuu-Owner-Probe`) |

## Timeline (UTC)

| When | Host | What |
|---|---|---|
| 10-30 21:15 | Thor | six: `forge-panes-e6257217` → `forge-gateway-8e43d044`. Ledger 117 → 118 (`000096`). PRESERVED. |
| 10-30 22:15 to 10-31 06:15 | mini | six's mini gate never opened: 884 checks in 8 h, at least two busy lanes each time. Not switched. |
| 10-31 06:31 | Thor | seven: → `forge-relay-58debfa5`. It switched under its own gate minutes after the driver was armed; the coordinator has since asked to hold Thor switches for its call (or its explicit go). |
| 10-31 06:56 to 07:34 | mini | seven's window: held by four stuck Claude background loops in two panes. The coordinator stopped them at 07:34. |
| 10-31 07:36 | mini | seven, straight from five: `forge-m6-panes-e6257217` → `forge-m6-relay-58debfa5`. Ledger 139\|44 → 140\|44. `guild.url` set to Thor. Six lanes restarted, every pane alive. |
| 10-31 07:44 | Thor | eight: → `forge-names-39ab05da`. PRESERVED. The mini stayed on seven (eight retired there). |
| 10-31 12:26 | Thor | push gateway enabled (config change and API restart). The first attempt at 12:22 rolled itself back: its check treated Spark's missing feed as a failure. |
| 10-31 12:32 | Thor | nine: `forge-names-39ab05da` → `forge-attn-c42970e6`. API ready after 5 s; smoke done at 12:32:41. PRESERVED (733 sessions). |
| 10-31 15:28 to 15:31 | mini | nine's window, called at 15:28:42. The gate opened at 15:31:20, once ios-fixes stopped its qa runs and its waiting loop. |
| 10-31 15:32 | mini | nine, straight from seven: `forge-m6-relay-58debfa5` → `forge-m6-attn-c42970e6`. |
| 10-31 16:08 | Thor | ten, first try: rolled back by its own smoke (`web /: 404`). The release was staged without its web dist (web-next unchanged, build skipped). Nothing else failed. |
| 10-31 16:13 | Thor | ten: `forge-attn-c42970e6` → `forge-guild-b25aec7f`, with nine's identical dist. PRESERVED (734 sessions). Spark feed requests: 1 in the first minute, against 89 per 8 min before. |
| 10-31 16:26 | Thor | eleven: → `forge-env-5aac51a5`, dist placed and checked by the new stager. PRESERVED (734 sessions). The mini's ten driver was retired; the mini goes nine → eleven in one window. |
| 10-31 17:41 | Thor | twelve's first build (`28e97992c`, `forge-owners-28e97992`): rolled back by its smoke. The zero-UUID relay read answered 502 "unreachable during owner lookup": a request loop between the facades (427 requests for one read), live on eleven since 17:16. Thor PRESERVED. |
| 10-31 18:55 to 18:58 | mini | twelve's window, called at 18:55:00. The gate opened at 18:56:35. Eleven → twelve (`forge-m6-owners-ec03df0c`): dark about 17 s (stop 18:57:32, ready 18:57:49), ledger 140\|44, 15 sessions, six panes verified at 18:58:16. |
| 10-31 19:02 | Thor | twelve (`forge-owners-ec03df0c`), its GO written automatically at 18:57:52 on the mini's CUTOVER_OK, so both facades run the loop guard. PRESERVED (734 sessions). Zero-UUID probes to the mini: 1; a mini session read: 0 requests to Spark. |
| 10-31 19:09 | Thor | thirteen: `forge-owners-ec03df0c` → `forge-fallback-6ff003fb`, API-only. PRESERVED (734 sessions). Smoke adds: a registration of an unknown kind answers 422 before anything is stored; no raw token in any registration or prune log line. |
| 10-31 21:10 to 21:21 | mini | thirteen's first window, called at 21:10 and stood down by the coordinator during Damien's live voice call. The driver had taken the GO and was waiting on the gate, which never re-checks the GO, so it was restarted to wait for a fresh one; nothing switched. |
| 10-31 22:47 to 22:50 | mini | thirteen's window, called at 22:47:15. Twelve → thirteen (`forge-m6-fallback-6ff003fb`): dark 14 s (stop 22:49:39, ready 22:49:53), ledger 140\|44, 15 sessions, six panes verified at 22:50:20. |
| 10-31 17:10 to 17:16 | mini | eleven's window, called at 17:10:22. The gate opened at 17:14:53, once lexi-macos's LEX-1741 Mac builds finished. Nine → eleven directly (`forge-m6-env-5aac51a5`): API dark 14 s (stop 17:15:50, ready 17:16:04), ledger 140\|44, 15 sessions before and after, six panes verified at 17:16:31. |

### Nine on the mini, second by second

- 15:31:42: preflight. The ledger is 140|44, and six lanes are running. The final busy check finds no busy lane or train.
- 15:31:45 to 15:32:36: the six lanes are stopped through the API, and their tmux servers ended.
- 15:32:38: `launchctl kickstart -k`. The API was ready at 15:32:44 and the smoke finished at 15:32:53.
  **The API was dark for 15 s.** The lanes were down from their stop until 15:33:18.
- 15:32:53: all six lanes restarted. By 15:33:19 every pane was alive and running a fresh Claude.

## The gate now names what blocks it

The busy-lane rule (runbook §1.3a, LEX-1706) holds a switch while a lane is busy:

- Forge reports the lane anything but idle;
- or its tmux pane runs a tool command or a train.

launchd and cron jobs (the nightly `qa.sh`) are outside the panes. They are logged, not waited for.

Since eight, each reason names one process, so a window can be cleared in a minute rather
than an hour. At 15:29, for example:

```
ios-fixes: pid 75254, 08:40, tool command: deadline=$((SECONDS+585)); until grep -q "^RESULT"
/tmp/lex1736-qa3.log || [ $SECONDS -gt $deadline ]; do sleep 20; done; …
```

A polling loop is never ignored by itself: it may run a follow-up step after it finishes.

## Smoke results

- **Thor, nine (12:32):**
  - sessions list 200 (84, including ios-coordinator); conversation and status reads 200;
  - web 200; Forge health through the Guild 200;
  - ledger 118; the LEX-1710 relay route answers; the devices route 200; a malformed
    `instance_id` gets 400;
  - the push gateway runs and reads Thor and mini-m6 (Spark unreachable: it has no feed);
  - PRESERVED, and the coordinator's conversation the same.
- **mini, nine (15:32):**
  - ledger 140|44, and a second run adds nothing; 15 sessions before and after;
  - conversation, status and forge-mcp reads 200; web 200; voice flags on; voice
    conversation 200; instance `mini-m6`;
  - Forge health through the Guild 200; the relay route answers; `guild.url` is Thor's;
  - a malformed `instance_id` gets 400.
- **Push gateway:** a test alert at 12:27 was accepted by APNs. A real attention push at
  15:21 (time-sensitive, with the full attention payload and `engine`) was accepted, 1 of 1
  devices, and Damien confirmed pushes arrive. No widget token is registered yet.

## Soak after nine

Two bounded checks of both hosts read health, the session list, a conversation, both
ledgers, the Guild view from Thor's registry, the mini's Guild URL and the push gateway.
Each would have messaged the coordinator on a problem; neither found one.

| When (UTC) | Thor | mini | Result |
|---|---|---|---|
| 16:05 | nine, 69 sessions (13 running), ledger 118 | nine, 15 sessions (6 running), ledger 140\|44 | 0 problems. Gateway since 12:32: 8 alerts sent, 0 failed, 19 Live Activity updates, 8 background wakes. |
| 16:35 | eleven (switched 16:26), same counts, ledger 118 | nine, same counts, ledger 140\|44 | 0 problems. Gateway counters restarted at 16:26; Spark under `feedless_nodes`, nothing unreachable. |

The 16:05 check also showed 14 APNs rejections, all pruned: 12 `ExpiredToken` (ended Live
Activities, expected) and 2 widget tokens rejected `BadDeviceToken` at 12:35 and 14:38,
registered by a dev build as production. Package 2 counts registrations per environment; a
sandbox token is routed correctly only when the app sends `sandbox` or `development`.

**Package 2's live sandbox check is pending.** Its proof for landing is the unit tests plus the
per-environment fields in Thor's `/push/status` on eleven (production: alert 1, Live Activity
3, Live Activity start 1; no registration with a missing or unknown environment). The live
check waits for the next sandbox token a Debug build registers on its own; then one sandbox
test alert goes to that token alone and APNs' answer is recorded here.

## LEX-1738 live check (17:17, mini on eleven)

From ios-release on the mini, using its own session credential, against niuu-consolidation
on Thor:

- `send_message` with Thor's id (`56499f00…`, the one the project context advertises) was
  delivered, relayed through the Guild. Before ten it answered 404 "Target not found".
- `get_session` of the Thor session answered 404 naming the owner: "owned by Thor
  (56499f00…) in this Guild", with how to reach it.
- `list_sessions` at Guild scope returned 85 sessions; 13 of the first 20 were Thor's.
- An `instance_id` that is not a UUID gave a clean 400; a session that exists nowhere gave
  a clean 404.
- Thor's Forge made no Spark feed request after eleven (89 per 8 min before ten).

Left for later: listed items carry `instance_id` but no `instance_name`, and two sessions'
`coordination.parent` still name the instance by its slug (`thor`) rather than its id.

## Twelve's rollback: the facades' loop

A read of a session id that exists nowhere went: Thor's full search asks the mini; the mini,
finding nothing, asks the Guild (Thor) who owns it (the LEX-1738 owner hint); Thor's full
search asks the mini again; and so on until the 5 s timeouts. It was live on eleven from the
mini's switch at 17:16 to twelve at 19:02. In that time no client re-read an unknown id; the
only storm was the smoke's own read at 17:41.

The fix (`ec03df0c3`): every owner probe and the owner-hint question carry
`X-Niuu-Owner-Probe`, and a facade answering one searches only its own store and never asks
the Guild. It only works once both facades run it (an old facade ignores the marker and asks
back), so twelve took one window: the mini first, Thor's GO written automatically on the
mini's CUTOVER_OK.

A lookup in which a node did not answer stays **502** (the node named in its detail and in
`X-Niuu-Source-Failures`); **404** only when every node answered no. A client may read 404
as "gone", and a sleeping node must never make a live session vanish.

## Widget tokens (LEX-1186)

Three production widget tokens from Damien's phone (12:35, 14:38, 17:19) were rejected
`BadDeviceToken` on their first reload and pruned. Forge's request matched Apple's
specification: host `api.push.apple.com`, topic `com.xteo.lexi.chat.push-type.widgets`,
`apns-push-type: widgets`. From thirteen the app registers the extension handler's token as
`widget` and its own `WidgetCenter.currentPushInfo` read as `widget_fallback`. The first
`widget_fallback` token arrived at 21:10:42 (fingerprint `e735acfff315`). One diagnostic
widget push to it, on the coordinator's order, also answered `BadDeviceToken`, so only the
handler path can work. No handler token has registered yet. The never-send rule for
`widget_fallback` stands.

## Lessons kept as rules

- Thor switches wait for the coordinator's call (or its explicit go for that promotion),
  like the mini's windows. Session restarts for ios-coordinator happen only when the
  broker or MCP changes, and at its call.
- The shared integration branch only ever gets a plain push.
- Every release carries its web app: `scripts/forge_release_dist.py` places it when web-next
  is unchanged and checks it before any GO (ten's first try rolled back on `web /: 404`).
- Tooling is never exercised against the release a host is serving; `place` refuses it.
- A change to how facades talk to each other ships to both hosts in one window, and its
  smoke runs with both on it; a two-facade test covers the case (no loop with the guard,
  a fast failure without it).
- An owner lookup with a node that did not answer is 502, not 404: absence is claimed only
  when every node answered.
- A called window that the coordinator stands down needs its waiting driver restarted: the
  gate loop does not re-check the GO.
- Config reads redact every credential-capable field (`dsn`, `url`, `password`, `token`,
  `key`) before printing. A secret that reaches a transcript is rotated the same hour: the
  Spark Forge database password was rotated on 10-31.
