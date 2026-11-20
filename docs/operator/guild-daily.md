# Guild daily summary

Each morning at 06:30 UTC a timer on Thor (`guild-daily.timer`) runs
`scripts/guild_daily_summary.py run ~/.config/guild-daily/config.json`. It reads Thor and
mini-m6 and appends one section below, and it sends ios-coordinator the same text as one
message. It only reads: no host, session or setting changes.

Each section reports:

- **Per host:** the build and revision served (and whether the tree is dirty), health, the
  migration ledger count, local sessions (and how many run), and 5xx answers in the window.
  Thor's are counted from its journal and split into those during cutover or rollback
  windows (from the rollouts' `restart=`/`stop=` … `end=`, widened by
  `cutover_margin_seconds`), those in `known_storms` (named spans in the config), and the
  rest, with their top `top_routes` routes (ids folded). The mini's log has no timestamps, so
  its 5xx are counted, not split, in what the log gained since the previous summary.
- **Push gateway:** the counters moved since the previous summary (or since the gateway
  started, when its log shows a start in the window), one line comparing those moves with
  the previous window's, APNs answers per environment,
  rejections by reason from the gateway's log (prune lines and refusals), registered tokens
  per environment and kind, and feedless and unreachable nodes.
- **Promotions:** cutovers rolled back and completed in the window, from the rollout folders'
  `chain.log` files, each named by its folder and host.

The window runs from the previous summary to this one (24 hours on the first run). Nothing
here holds a secret: build names, counts, token fingerprints at most, never a token, DSN or
key. The message goes first; then the job commits only this file, with a plain push. A
failed commit or a refused push is sent as a short second message and never forced; the
next run carries a section that never landed (uncommitted, or committed but not pushed)
ahead of its own. The service's PATH includes
`~/.local/bin`, where the repository's pre-commit hooks (the TruffleHog secret scan) live:
without it the hook fails and git refuses the commit (the first run, 2026-11-01 06:30).

Configuration (`~/.config/guild-daily/config.json`, mode 600, outside the repository): the
coordinator's session id, the Forge and push status URLs, the gateway's journal unit, the
rollout folder, the state file, the docs worktree (the job's own, kept clean) and branch,
`first_window_hours` (the first run's window), `http_timeout_seconds`, and per host a URL,
a ledger command (`{build}` is the served build) and how to read its errors (a journal
unit, or a size command and a count command for a log without timestamps). To pause it, run
`systemctl --user disable --now guild-daily.timer`.

## Guild summary 2026-11-01 (2026-10-31 06:33 to 2026-11-01 06:33 UTC)

- **Thor**: ok, forge-projects-4cd719ba (4cd719bad, not dirty); ledger 118; 71 sessions (15 running); 5xx 482
- **mini-m6**: ok, forge-m6-projects-4cd719ba (4cd719bad, not dirty); ledger 140|44; 15 sessions (6 running); 5xx 0 (since 11-01 01:08)

- **Push gateway** (running), since the gateway started: alerts_sent 8, alerts_suppressed 2, live_activity_updates 53, background_wakes 9, apns_rejections 8, tokens_pruned 8, node_failures 3, loop_errors 1
  - APNs answers per environment, since the gateway started: production: accepted 70, rejected 8
  - Rejections by reason in this window (gateway log): HTTP 410 ExpiredToken (token pruned) ×23, HTTP 400 BadDeviceToken (token pruned) ×4
  - Registered tokens: production: alert 1, live_activity 3, live_activity_start 1, widget_fallback 1
  - Feedless nodes: 582bf8d0-41fb-4c5d-ad4c-e5cdbbdeb0c8; unreachable: none

- **Promotions**: rolled back 2 (10-31 16:09 Thor forge-guild-b25aec7f; 10-31 17:42 Thor forge-owners-28e97992); completed 13 (10-31 07:36 mini forge-relay-58debfa5; 10-31 07:44 Thor forge-names-39ab05da; 10-31 12:32 Thor forge-attn-c42970e6; 10-31 15:33 mini forge-attn-c42970e6; 10-31 16:13 Thor forge-guild-b25aec7f; 10-31 16:27 Thor forge-env-5aac51a5; 10-31 17:16 mini forge-env-5aac51a5; 10-31 18:58 mini forge-owners-ec03df0c; 10-31 19:02 Thor forge-owners-ec03df0c; 10-31 19:09 Thor forge-fallback-6ff003fb; 10-31 22:50 mini forge-fallback-6ff003fb; 10-31 23:05 Thor forge-projects-4cd719ba; 11-01 01:01 mini forge-projects-4cd719ba)

## Guild summary 2026-11-02 (2026-11-01 06:33 to 2026-11-02 06:30 UTC)

- **Thor**: ok, forge-projects-4cd719ba (4cd719bad, not dirty); ledger 118; 78 sessions (16 running); 5xx 8 (0 during cutovers or rollbacks, 0 in known storms, 8 other: GET /s/{id}/api/conversation/history ×4, GET /api/v1/forge/sessions/{id}/conversation ×3, GET /api/v1/forge/sessions/{id} ×1)
- **mini-m6**: ok, forge-m6-projects-4cd719ba (4cd719bad, not dirty); ledger 140|44; 15 sessions (6 running); 5xx 0

- **Push gateway** (running), since 11-01 06:33: alerts_sent 49, alerts_suppressed 7, live_activity_updates 349, background_wakes 42, apns_rejections 13, tokens_pruned 13, node_failures 13
  - APNs answers per environment, since 11-01 06:33: production: accepted 440, rejected 13
  - Rejections by reason in this window (gateway log): HTTP 410 ExpiredToken (token pruned) ×13
  - Registered tokens: production: alert 1, live_activity 7, live_activity_start 1, widget_fallback 1
  - Feedless nodes: 582bf8d0-41fb-4c5d-ad4c-e5cdbbdeb0c8; unreachable: none

- **Promotions**: rolled back 0; completed 0

## Guild summary 2026-11-03 (2026-11-02 06:30 to 2026-11-03 06:30 UTC)

- **Thor**: ok, forge-lateframe-410e5b9c (410e5b9ca, not dirty); ledger 118; 82 sessions (18 running); 5xx 45 (0 during cutovers or rollbacks, 0 in known storms, 45 other: GET /s/{id}/api/conversation/history ×22, GET /api/v1/forge/sessions/{id}/conversation ×20, GET /s/{id}/health ×2)
- **mini-m6**: ok, forge-m6-projects-4cd719ba (4cd719bad, not dirty); ledger 140|44; 16 sessions (6 running); 5xx 0

- **Push gateway** (running), since the gateway started: loop_errors 1
  - Against the previous window: alerts_sent 49 → 0, alerts_suppressed 7 → 0, live_activity_updates 349 → 0, background_wakes 42 → 0, apns_rejections 13 → 0, tokens_pruned 13 → 0, node_failures 13 → 0, loop_errors 0 → 1
  - APNs answers per environment, since the gateway started: none
  - Rejections by reason in this window (gateway log): HTTP 410 ExpiredToken (token pruned) ×11
  - Registered tokens: production: alert 1, live_activity 1, live_activity_start 1, widget_fallback 1; sandbox: alert 1, live_activity 2, live_activity_start 1, widget 1
  - Feedless nodes: 582bf8d0-41fb-4c5d-ad4c-e5cdbbdeb0c8; unreachable: none

- **Promotions**: rolled back 0; completed 1 (11-03 03:05 Thor forge-lateframe-410e5b9c)

## Guild summary 2026-11-04 (2026-11-03 06:30 to 2026-11-04 06:30 UTC)

- **Thor**: ok, forge-orphans-3e2ee9f6 (3e2ee9f6a, not dirty); ledger 118; 78 sessions (12 running); 5xx 147 (0 during cutovers or rollbacks, 0 in known storms, 147 other: GET /s/{id}/api/conversation/history ×109, GET /api/v1/forge/sessions/{id}/conversation ×37, GET /api/v1/forge/notifications ×1)
- **mini-m6**: ok, forge-m6-projects-4cd719ba (4cd719bad, not dirty); ledger unreadable (CalledProcessError); 17 sessions (6 running); 5xx 0

- **Push gateway** (running), since the gateway started: alerts_sent 2, alerts_suppressed 1, widget_reloads 3, background_wakes 6, apns_rejections 3, tokens_pruned 3, loop_errors 1
  - Against the previous window: alerts_sent 0 → 2, alerts_suppressed 0 → 1, widget_reloads 0 → 3, background_wakes 0 → 6, apns_rejections 0 → 3, tokens_pruned 0 → 3
  - APNs answers per environment, since the gateway started: production: accepted 5, rejected 2; sandbox: accepted 8, rejected 1
  - Rejections by reason in this window (gateway log): HTTP 410 ExpiredToken (token pruned) ×15, HTTP 410 Unregistered (token pruned) ×4, HTTP 400 BadDeviceToken (token pruned) ×1
  - Registered tokens: production: alert 1, live_activity_start 2, widget_fallback 1; sandbox: alert 1, live_activity_start 1, widget 1
  - Feedless nodes: 582bf8d0-41fb-4c5d-ad4c-e5cdbbdeb0c8; unreachable: none

- **Promotions**: rolled back 1 (11-03 08:07 Thor forge-errturn-426ec48f); completed 2 (11-03 08:13 Thor forge-errturn-426ec48f; 11-04 02:01 Thor forge-orphans-3e2ee9f6)

## Guild summary 2026-11-05 (2026-11-04 06:30 to 2026-11-05 06:30 UTC)

- **Thor**: ok, forge-sender-9338b0be (9338b0bea, not dirty); ledger 118; 80 sessions (13 running); 5xx 31 (0 during cutovers or rollbacks, 0 in known storms, 31 other: GET /s/{id}/api/conversation/history ×16, GET /api/v1/forge/sessions/{id}/conversation ×15)
- **mini-m6**: ok, forge-m6-projects-4cd719ba (4cd719bad, not dirty); ledger 140|44; 17 sessions (6 running); 5xx 0

- **Push gateway** (running), since the gateway started: alerts_sent 5, live_activity_updates 11, widget_reloads 5, background_wakes 10, apns_rejections 2, tokens_pruned 2, loop_errors 1
  - Against the previous window: alerts_sent 2 → 5, alerts_suppressed 1 → 0, live_activity_updates 0 → 11, widget_reloads 3 → 5, background_wakes 6 → 10, apns_rejections 3 → 2, tokens_pruned 3 → 2
  - APNs answers per environment, since the gateway started: production: accepted 21, rejected 2; sandbox: accepted 15
  - Rejections by reason in this window (gateway log): HTTP 410 ExpiredToken (token pruned) ×11
  - Registered tokens: production: alert 1, live_activity 2, live_activity_start 2, widget_fallback 1; sandbox: alert 1, live_activity_start 1, widget 1
  - Feedless nodes: 582bf8d0-41fb-4c5d-ad4c-e5cdbbdeb0c8; unreachable: none

- **Promotions**: rolled back 0; completed 0

## Guild summary 2026-11-06 (2026-11-05 06:30 to 2026-11-06 06:30 UTC)

- **Thor**: ok, forge-twenty-4a2fb9a5 (4a2fb9a5b, not dirty); ledger 119; 82 sessions (14 running); 5xx 14 (0 during cutovers or rollbacks, 0 in known storms, 14 other: GET /s/{id}/api/conversation/history ×6, GET /api/v1/forge/sessions/{id}/conversation ×5, GET /api/v1/forge/notifications ×3)
- **mini-m6**: ok, forge-m6-questions-2607a181 (2607a181b, not dirty); ledger 141|44; 17 sessions (6 running); 5xx 0

- **Push gateway** (running), since the gateway started: alerts_sent 2, alerts_suppressed 1, live_activity_updates 2, widget_reloads 3, background_wakes 4, loop_errors 1
  - Against the previous window: alerts_sent 5 → 2, alerts_suppressed 0 → 1, live_activity_updates 11 → 2, widget_reloads 5 → 3, background_wakes 10 → 4, apns_rejections 2 → 0, tokens_pruned 2 → 0
  - APNs answers per environment, since the gateway started: production: accepted 8; sandbox: accepted 9
  - Rejections by reason in this window (gateway log): HTTP 410 ExpiredToken (token pruned) ×16
  - Registered tokens: production: alert 1, live_activity 1, live_activity_start 3, widget_fallback 1; sandbox: alert 1, live_activity_start 1, widget 1
  - Feedless nodes: 582bf8d0-41fb-4c5d-ad4c-e5cdbbdeb0c8; unreachable: none

- **Promotions**: rolled back 0; completed 0

## Guild summary 2026-11-07 (2026-11-06 06:30 to 2026-11-07 06:30 UTC)

- **Thor**: ok, forge-contract-b456e6d3 (b456e6d3b, not dirty); ledger 119; 85 sessions (16 running); 5xx 14 (0 during cutovers or rollbacks, 0 in known storms, 14 other: GET /s/{id}/api/conversation/history ×8, GET /s/{id}/health ×6)
- **mini-m6**: ok, forge-m6-questions-2607a181 (2607a181b, not dirty); ledger 141|44; 17 sessions (6 running); 5xx 0

- **Push gateway** (running), since the gateway started: alerts_sent 11, alerts_suppressed 3, live_activity_updates 83, widget_reloads 14, background_wakes 24, apns_rejections 7, tokens_pruned 7, loop_errors 1
  - Against the previous window: alerts_sent 2 → 11, alerts_suppressed 1 → 3, live_activity_updates 2 → 83, widget_reloads 3 → 14, background_wakes 4 → 24, apns_rejections 0 → 7, tokens_pruned 0 → 7
  - APNs answers per environment, since the gateway started: production: accepted 107, rejected 7; sandbox: accepted 38
  - Rejections by reason in this window (gateway log): HTTP 410 ExpiredToken (token pruned) ×13, HTTP 410 Unregistered (token pruned) ×1
  - Registered tokens: production: alert 1, live_activity 2, live_activity_start 3, widget_fallback 1; sandbox: alert 1, live_activity_start 1, widget 1
  - Feedless nodes: 582bf8d0-41fb-4c5d-ad4c-e5cdbbdeb0c8; unreachable: none

- **Promotions**: rolled back 0; completed 0

## Guild summary 2026-11-09 (2026-11-07 06:30 to 2026-11-09 06:30 UTC)

- **Thor**: ok, forge-span-30f20f4f (30f20f4f2, not dirty); ledger 119; 84 sessions (9 running); 5xx 99 (0 during cutovers or rollbacks, 0 in known storms, 99 other: GET /api/v1/forge/sessions/{id}/native-controls ×41, POST /api/v1/forge/sessions/{id}/messages ×39, GET /s/{id}/health ×6)
- **mini-m6**: ok, forge-m6-span-30f20f4f (30f20f4f2, not dirty); ledger 141|44; 17 sessions (6 running); 5xx 0

- **Push gateway** (running), since the gateway started: alerts_sent 19, alerts_suppressed 1, live_activity_updates 112, widget_reloads 20, background_wakes 40, apns_rejections 15, tokens_pruned 15, node_failures 2, loop_errors 1
  - Against the previous window: alerts_sent 11 → 19, alerts_suppressed 3 → 1, live_activity_updates 83 → 112, widget_reloads 14 → 20, background_wakes 24 → 40, apns_rejections 7 → 15, tokens_pruned 7 → 15, node_failures 0 → 2
  - APNs answers per environment, since the gateway started: production: accepted 152, rejected 15; sandbox: accepted 60
  - Rejections by reason in this window (gateway log): HTTP 410 ExpiredToken (token pruned) ×27
  - Registered tokens: production: alert 1, live_activity_start 3, widget_fallback 1; sandbox: alert 1, live_activity_start 1, widget 1
  - Feedless nodes: 582bf8d0-41fb-4c5d-ad4c-e5cdbbdeb0c8; unreachable: none

- **Promotions**: rolled back 0; completed 0

## Guild summary 2026-11-10 (2026-11-09 06:30 to 2026-11-10 06:30 UTC)

- **Thor**: ok, forge-switch-0666439b (0666439ba, not dirty); ledger 119; 85 sessions (10 running); 5xx 44 (0 during cutovers or rollbacks, 0 in known storms, 44 other: GET /s/{id}/health ×20, GET /s/{id}/api/conversation/history ×14, GET /s/{id}/api/agents ×3)
  - Questions closed without an answer, by stage.reason, since each broker started: none; 1 unreadable
- **mini-m6**: ok, forge-m6-switch-0666439b (0666439ba, not dirty); ledger 141|44; 17 sessions (6 running); 5xx 0
  - Questions closed without an answer, by stage.reason, since each broker started: reconcile_start.never_shown 4; 1 broker(s) on older code, without counts

- **Push gateway** (running), since the gateway started: alerts_sent 7, alerts_suppressed 2, live_activity_updates 15, widget_reloads 9, background_wakes 16, apns_rejections 2, tokens_pruned 2, node_failures 1, loop_errors 1
  - Against the previous window: alerts_sent 19 → 7, alerts_suppressed 1 → 2, live_activity_updates 112 → 15, widget_reloads 20 → 9, background_wakes 40 → 16, apns_rejections 15 → 2, tokens_pruned 15 → 2, node_failures 2 → 1
  - APNs answers per environment, since the gateway started: production: accepted 33, rejected 2; sandbox: accepted 27
  - Rejections by reason in this window (gateway log): HTTP 410 ExpiredToken (token pruned) ×4
  - Registered tokens: production: alert 1, live_activity 2, live_activity_start 3, widget_fallback 1; sandbox: alert 1, live_activity_start 1, widget 1
  - Feedless nodes: 582bf8d0-41fb-4c5d-ad4c-e5cdbbdeb0c8; unreachable: none

- **Promotions**: rolled back 0; completed 0

## Guild summary 2026-11-11 (2026-11-10 06:30 to 2026-11-11 06:30 UTC)

- **Thor**: ok, forge-le-d67e62d7 (d67e62d76, not dirty); ledger 119; 87 sessions (15 running); 5xx 34 (0 during cutovers or rollbacks, 0 in known storms, 34 other: GET /api/v1/forge/sessions/{id}/conversation ×18, GET /s/{id}/health ×6, GET /api/v1/forge/notifications ×4)
  - Questions closed without an answer, by stage.reason, since each broker started: none; 1 unreadable
- **mini-m6**: ok, forge-m6-le-d67e62d7 (d67e62d76, not dirty); ledger 141|44; 18 sessions (6 running); 5xx 0
  - Questions closed without an answer, by stage.reason, since each broker started: none

- **Push gateway** (running), since the gateway started: alerts_sent 16, alerts_suppressed 5, live_activity_updates 30, widget_reloads 18, background_wakes 22, apns_rejections 1, tokens_pruned 1, node_failures 2, loop_errors 1
  - Against the previous window: alerts_sent 7 → 16, alerts_suppressed 2 → 5, live_activity_updates 15 → 30, widget_reloads 9 → 18, background_wakes 16 → 22, apns_rejections 2 → 1, tokens_pruned 2 → 1, node_failures 1 → 2
  - APNs answers per environment, since the gateway started: production: accepted 64, rejected 1; sandbox: accepted 52
  - Rejections by reason in this window (gateway log): not reached (ConnectError) ×14, HTTP 410 ExpiredToken (token pruned) ×9, not reached (ConnectTimeout) ×4
  - Registered tokens: production: alert 1, live_activity 3, live_activity_start 3, widget_fallback 1; sandbox: alert 1, live_activity_start 1, widget 1
  - Feedless nodes: 582bf8d0-41fb-4c5d-ad4c-e5cdbbdeb0c8; unreachable: none

- **Promotions**: rolled back 0; completed 0

## Guild summary 2026-11-12 (2026-11-11 06:30 to 2026-11-12 06:30 UTC)

- **Thor**: ok, forge-le-d67e62d7 (d67e62d76, not dirty); ledger 119; 94 sessions (16 running); 5xx 15 (0 during cutovers or rollbacks, 0 in known storms, 15 other: GET /s/{id}/api/conversation/history ×12, GET /s/{id}/health ×2, GET /api/v1/forge/sessions/{id}/conversation ×1)
  - Questions closed without an answer, by stage.reason, since each broker started: none
- **mini-m6**: ok, forge-m6-le-d67e62d7 (d67e62d76, not dirty); ledger 141|44; 18 sessions (6 running); 5xx 0
  - Questions closed without an answer, by stage.reason, since each broker started: none

- **Push gateway** (running), since 11-11 06:30: alerts_sent 43, alerts_suppressed 10, live_activity_updates 353, widget_reloads 53, background_wakes 80, apns_rejections 20, tokens_pruned 20, node_failures 1
  - Against the previous window: alerts_sent 16 → 43, alerts_suppressed 5 → 10, live_activity_updates 30 → 353, widget_reloads 18 → 53, background_wakes 22 → 80, apns_rejections 1 → 20, tokens_pruned 1 → 20, node_failures 2 → 1, loop_errors 1 → 0
  - APNs answers per environment, since 11-11 06:30: production: accepted 425, rejected 18; sandbox: accepted 171, rejected 2
  - Rejections by reason in this window (gateway log): HTTP 410 ExpiredToken (token pruned) ×19, HTTP 410 Unregistered (token pruned) ×1
  - Registered tokens: production: alert 1, live_activity 1, live_activity_start 3, widget_fallback 1; sandbox: alert 1, live_activity_start 1, widget 1
  - Feedless nodes: 582bf8d0-41fb-4c5d-ad4c-e5cdbbdeb0c8; unreachable: none

- **Promotions**: rolled back 0; completed 0

## Guild summary 2026-11-13 (2026-11-12 06:30 to 2026-11-13 06:30 UTC)

- **Thor**: ok, forge-le-d67e62d7 (d67e62d76, not dirty); ledger 119; 113 sessions (15 running); 5xx 26 (0 during cutovers or rollbacks, 0 in known storms, 26 other: GET /s/{id}/api/conversation/history ×24, PUT /api/v1/forge/sessions/{id}/project ×1, POST /api/v1/forge/sessions/{id}/messages ×1)
  - Questions closed without an answer, by stage.reason, since each broker started: none
- **mini-m6**: ok, forge-m6-le-d67e62d7 (d67e62d76, not dirty); ledger 141|44; 18 sessions (2 running); 5xx 0
  - Questions closed without an answer, by stage.reason, since each broker started: none

- **Push gateway** (running), since 11-12 06:30: alerts_sent 49, alerts_suppressed 16, live_activity_updates 241, widget_reloads 62, background_wakes 88, apns_rejections 9, tokens_pruned 9, node_failures 3
  - Against the previous window: alerts_sent 43 → 49, alerts_suppressed 10 → 16, live_activity_updates 353 → 241, widget_reloads 53 → 62, background_wakes 80 → 88, apns_rejections 20 → 9, tokens_pruned 20 → 9, node_failures 1 → 3
  - APNs answers per environment, since 11-12 06:30: production: accepted 358, rejected 9; sandbox: accepted 179, rejected 0
  - Rejections by reason in this window (gateway log): HTTP 410 ExpiredToken (token pruned) ×8, HTTP 400 BadDeviceToken (token pruned) ×1
  - Registered tokens: production: alert 1, live_activity 3, live_activity_start 4, widget_fallback 1; sandbox: alert 1, live_activity_start 1, widget 1
  - Feedless nodes: 582bf8d0-41fb-4c5d-ad4c-e5cdbbdeb0c8; unreachable: none

- **Promotions**: rolled back 0; completed 0
