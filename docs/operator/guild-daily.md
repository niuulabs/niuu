# Guild daily summary

Each morning at 06:30 UTC a timer on Thor (`guild-daily.timer`) runs
`scripts/guild_daily_summary.py run ~/.config/guild-daily/config.json`. It reads Thor and
mini-m6 and appends one section below, and it sends ios-coordinator the same text as one
message. It only reads: no host, session or setting changes.

Each section reports:

- **Per host:** the build and revision served (and whether the tree is dirty), health, the
  migration ledger count, local sessions (and how many run), and 5xx answers in the window.
  Thor's are counted from its journal. The mini's log has no timestamps, so they are counted
  in what the log gained since the previous summary.
- **Push gateway:** the counters moved since the previous summary (or since the gateway
  started, when its log shows a start in the window), APNs answers per environment,
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
