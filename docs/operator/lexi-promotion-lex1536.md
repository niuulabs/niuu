# Promotion note: the `speak` intent on Forge notifications (LEX-1536)

**Status: PREPARED, not applied.** The coordinator schedules the promotion to Thor
and mini-m6 with Damien. Nothing on either host was changed while preparing it:
the live databases were only read (`SELECT`, a schema-only `pg_dump`), and the
proofs ran on a scratch copy on 127.0.0.1 that was removed afterwards.

It is a small release on top of B3 (`docs/operator/lexi-promotion-20261026.md`).
Use that runbook's mechanics unchanged, with the differences listed here.

| | Thor (Home Forge, `:8080`) | mini-m6 (`100.92.69.62:8080`) |
|---|---|---|
| Runs today | `forge-consolidated-20261026` (59c974c8a) | `forge-m6-consolidated-20261026` (59c974c8a) |
| Promote to | `forge-speak-66ff345c` = `lexi/dev-api-integration` **66ff345c9** (`66ff345c9e6bdeace0e60abd93cf4c78459eea71`; docs after it are allowed) | `forge-m6-speak-66ff345c`, same sha |
| Migrations at boot | **1 executed** (`000094`), nothing adopted | **1 executed** (`000094`), nothing adopted; `ting/` unchanged |
| Config change | **none** | **none** |
| Web | rebuild (the "Spoken" badge) and swap, as B3 §4.1 step 6 | rebuild, as B3 §4.2 |
| Expected API downtime | ≈ 20 s (B3 attempt 2: 18 s) | ≈ 6 s (B3: 6 s), plus restarting the sessions it stops |
| Rollback target | `forge-consolidated-20261026` | `forge-m6-consolidated-20261026` |
| Point of no return | none | none |

## 1. What changes

An agent notification (milestone, decision, attention, error, info; never
`reply_ready`) may carry `speak: {text, priority}`: `text` is 1–600 characters
after trimming, `priority` is `now` (default) or `next_window`. The Forge MCP
`notify` tool (transcript and feed modes) and `POST /sessions/{id}/notifications`
accept it; an invalid one is a tool error or HTTP 422 and records nothing. Forge
stores it and returns `speak` on `GET /notifications`, the per-session list, the
submit response and the `session_notification` live event (`null` when absent).
The web shows a "Spoken" badge. **Forge delivers and speaks nothing**; LEX-1535
(agent service) decides whether the phone speaks.

Contract: `docs/forge/session-notifications-contract.md` §13. Operator view:
`docs/operator/forge-notifications.md` § Speak intent.

## 2. The migration

`migrations/000094_forge_notification_speak.up.sql`,
sha256 `f29350b3570c90d4d55455951a563f998943ab86b1c7f2ed870dee6c3be4482c`
(pinned by `tests/test_adapters/test_notification_speak.py`).

- `ALTER TABLE forge_notifications ADD COLUMN IF NOT EXISTS speak_text TEXT NULL`
  and `speak_priority VARCHAR(16) NULL`: no default, so no table rewrite.
- `forge_notifications_speak_check` (added only if absent): both columns null, or
  both set with 1–600 characters, a known priority and a kind other than
  `reply_ready`. Adding it scans the table once under the ALTER's lock: 461 rows
  on Thor and 293 on m6 at prep time.
- No lineage alias: no host ever applied this SQL under another name. Upstream
  `dev` ends at `000088`, so the number is free.

**Dry run (2026-10-27)** on a scratch PostgreSQL 16 holding Thor's full schema and
its `forge_notifications`, `forge_notification_images` and `volundr_schema_history`
rows, with the release tree's own `apply_startup_migrations` (Volundr stream, as
Thor boots):

- ledger **115 → 116**: `000094_forge_notification_speak.up.sql` executed with the
  sha above; all 94 files verified in 0.03 s.
- Second run: no-op (116). The B3 release's runner (93 files) on the result:
  no-op (116).
- Rollback probe: the B3 release's `PostgresNotificationRepository` on the
  migrated copy inserted, read and listed a notification normally (the old code
  names its INSERT columns and ignores the new ones). The new adapter stored and
  read back a `speak`.
- The check refused each bad row it should: text without priority, empty text,
  priority `soon`, 601 characters, `speak` on `reply_ready`. (The first run of the
  drill found that a lone `speak_text` passed, because `NULL IN (…)` is not false;
  the shipped SQL requires both columns explicitly.)

m6's `forge_notifications` has the same columns as Thor's (checked read-only), and
neither host has a `forge_notifications_speak_check` yet; m6 runs the same Volundr
stream plus `ting/`, which this release does not change.

**Pre-checks** (read-only, right before each switch):

```bash
q() { docker exec volundr-pg psql -U volundr -d volundr -Atc "$1"; }        # Thor
q "SELECT count(*), max(filename) FROM volundr_schema_history"              # 115|000093_forge_artifact_publications.up.sql
mq() { ssh xteo@mini-m6 "~/.local/share/niuu/releases/forge-m6-consolidated-20261026/build/pginstall/bin/psql \
         -h ~/.local/share/niuu-forge/pgdata -U postgres -d volundr -Atc \"$1\""; }
mq "SELECT count(*), count(*) FILTER (WHERE filename LIKE 'ting/%') FROM volundr_schema_history"   # 137|44
mq "SELECT max(filename) FROM volundr_schema_history WHERE filename NOT LIKE 'ting/%'"             # 000093_forge_artifact_publications.up.sql
# both hosts: no long transaction may hold forge_notifications (the ALTERs wait for its lock)
```

Stop condition: any other count or last row.

**Post-checks:**

```bash
q  "SELECT count(*), max(filename) FROM volundr_schema_history"   # 116|000094_forge_notification_speak.up.sql
q  "SELECT sha256 FROM volundr_schema_history WHERE filename = '000094_forge_notification_speak.up.sql'"
#  f29350b3570c90d4d55455951a563f998943ab86b1c7f2ed870dee6c3be4482c
mq "SELECT count(*), count(*) FILTER (WHERE filename LIKE 'ting/%') FROM volundr_schema_history"   # 138|44
```

## 3. Build and switch

As B3, with the new names and sha:

- **Thor:** build per B3 §2.2 (`NAME=forge-speak-66ff345c`, worktree at
  66ff345c9, `uv sync --frozen`, web `pnpm install --frozen-lockfile && pnpm build`).
  Switch per B3 §7: take a fresh backup first (B3 §1.2), launch the attempt-2 cutover
  (`cutover2.sh`, fail-fast) under `systemd-run --user --unit <name> --collect`,
  with the drop-in one `z` longer than the B3 one and the same launcher
  (`start_platform_b3.py`, Ting off). `preserve_check.py` before/after must print
  PRESERVED. Then the web swap (B3 §4.1 step 6) to the new `dist`.
- **mini-m6:** prepare the release on m6 the day before (B3 §4.2), then run the
  `cutover-m6.sh` driver from Thor under `systemd-run` (B3 §8.2). The plist does
  not change this time, but `bootout` + `bootstrap` is still the switch the
  driver knows. Running brokers die with the job (B3 §8.4 is unproven): the
  coordinator stops and restarts m6's sessions around it, and keeps one slot free
  for the smoke session (`max_concurrent: 6`, B3 §8.3).
- No config key is added or changed on either host.

## 4. Smoke

B3 §4.3 rows **1** (build: `revision` 66ff345c9, `dirty: false`,
`failed_plugins: []`), **2** (sessions list), **7** (scratch session), **9**
(`notify`), **12** (web) and **13** (cleanup) still apply. Add these, in the
scratch session, `F=$A/api/v1/forge`:

| # | Check | Command / action | Expect |
|---|---|---|---|
| S1 | MCP `speak` | Forge MCP `notify`, kind `info`, title "LEX-1536 smoke", `speak: {"text": "LEX-1536 smoke.", "priority": "next_window"}` | `state: committed`; `curl -s "$F/notifications?limit=5"` shows the item with `"speak":{"text":"LEX-1536 smoke.","priority":"next_window"}`; older items show `"speak":null` |
| S2 | per-session list | `curl -s "$F/sessions/<scratch id>/notifications"` | the same `speak` object |
| S3 | live event | `curl -sN "$F/sessions/stream" > /tmp/sse.txt &` before S1, stop it after | the `session_notification` event for S1 carries the same `speak` |
| S4 | REST validation | `curl -s -o /dev/null -w '%{http_code}' -X POST "$F/sessions/<scratch id>/notifications" -H 'content-type: application/json' -d '{"kind":"info","title":"x","idempotency_key":"lex1536-bad","speak":{"text":" "}}'` and the same with `"kind":"reply_ready","speak":{"text":"x"}` | `422` both times; nothing recorded |
| S5 | ledger | the post-checks in §2 | as stated |
| S6 | web | the feed (Activities) in the new web build | S1's row shows a "Spoken" badge; its tooltip reads "To be spoken at the next good moment: LEX-1536 smoke." |

S1 goes through Damien's delivery rules like any `notify`; say so beforehand.
Until LEX-1535 ships, the agent service ignores `speak`; after it ships, S1 may be
spoken, which is why it uses `next_window`.

Red in rows 1–2 or S5 means rolling back. Red in S1–S4 or S6 is the feature:
record it, and roll back only if Damien asks.

## 5. Rollback

B3 §5, to the B3 releases: Thor removes the new drop-in (the B3 one takes over)
and restarts; m6 restores the B3 launcher and re-bootstraps. Leave the database as
it is: the two columns, the check and the ledger row stay, and the B3 code works
on them (§2 rollback probe). It never writes `speak_*`, so the check cannot refuse
its inserts, and stored `speak` values simply go unread until the release
returns. Re-promoting later runs nothing (the ledger row is already there).
