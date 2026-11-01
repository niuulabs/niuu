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
next run rewrites a section that was never committed. The service's PATH includes
`~/.local/bin`, where the repository's pre-commit hooks (the TruffleHog secret scan) live:
without it the hook fails and git refuses the commit (the first run, 2026-11-01 06:30).

Configuration (`~/.config/guild-daily/config.json`, mode 600, outside the repository): the
coordinator's session id, the Forge and push status URLs, the gateway's journal unit, the
rollout folder, the state file, the docs worktree (the job's own, kept clean) and branch,
`first_window_hours` (the first run's window), `http_timeout_seconds`, and per host a URL,
a ledger command (`{build}` is the served build) and how to read its errors (a journal
unit, or a size command and a count command for a log without timestamps). To pause it, run
`systemctl --user disable --now guild-daily.timer`.
