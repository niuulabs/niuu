# Promotion note: Forge MCP grants and preview size (LEX-1506, LEX-1655)

**Status: APPLIED 2026-10-30.** Thor switched at 08:53:45 UTC and mini-m6 at
08:57:00 UTC. Both passed their smoke, and nothing was rolled back. The record is
in §Switch record below and in `~/.local/share/niuu/rollouts/forge-grants-ef6dead3/`
on Thor.

This release replaces the prepared LEX-1536 release
(`docs/operator/lexi-promotion-lex1536.md`). It contains that release, so the
`speak` intent goes live in the same switch. Use that note's mechanics, checks and
rollback unchanged, with the names and sha below.

| | Thor (Home Forge, `:8080`) | mini-m6 (`100.92.69.62:8080`) |
|---|---|---|
| Runs today | `forge-consolidated-20261026` (59c974c8a), clean tree | `forge-m6-consolidated-20261026` (59c974c8a), clean tree |
| Promote to | `forge-grants-ef6dead3` = `lexi/dev-api-integration` **ef6dead37** (`ef6dead37ecb5968de25ae14e95b734d0bc1d6a5`) | `forge-m6-grants-ef6dead3`, same sha |
| Built | `~/.local/share/niuu/releases/forge-grants-ef6dead3`: venv and web dist; `build_identity` gives `dirty: False` | `~/.local/share/niuu/releases/forge-m6-grants-ef6dead3`: venv, `pginstall` symlink, web dist rsynced; `dirty: False` |
| Web staged | `~/.local/share/niuu-forge-web/releases/forge-grants-ef6dead3` (live `config.json`/`config.live.json` copied, `demoMode: false`) | served from the release |
| Migrations at boot | **1 executed** (`000094`), as in the LEX-1536 note §2 | the same, with `ting/` unchanged |
| Config change | **none**. The new keys use their defaults, checked against each host's live file. | **none** |
| Rollback target | `forge-consolidated-20261026` | `forge-m6-consolidated-20261026` |

## What goes live

- **LEX-1536:** `speak` on notifications (see that note).
- **LEX-1506:**
  - `GET`/`PUT /api/v1/forge/sessions/{id}/forge-mcp`. It is operator-only and
    audited; see `docs/operator/forge-mcp.md`.
  - `forge_mcp.role_grants`, by default `coordinator: [lifecycle, message]`. Every
    session filed as a project coordinator gets both grants **at its next start**.
    Running sessions keep their current credential until then.
- **LEX-1655:** `?max_edge=` on the tool-result preview, with the headers
  `X-Preview-Max-Edge` and `X-Preview-Edge`. The default stays 400 px.

## Web on Thor

Thor's live web is still `forge-ux-cleanup-31906a0a-midnight-questions-27b4ff14`;
B3 did not swap it. The staged build is the consolidated app. The coordinator
decides whether to swap it in this switch (B3 §4.1 step 6) or to leave it as it is.
The API changes need no web change.

## Extra smoke (after the LEX-1536 rows)

Run these against the scratch session. Set `F=$A/api/v1/forge` and `S=<scratch id>`.

| # | Check | Command | Expect |
|---|---|---|---|
| G1 | read grants | `curl -s $F/sessions/$S/forge-mcp` | `grants: []` and `applies_at: "next_start"` |
| G2 | set grants, audited | `curl -s -X PUT $F/sessions/$S/forge-mcp -H 'content-type: application/json' -d '{"grants":["message"]}'`, then `curl -s "$F/../audit/events?event_type=volundr.session.forge_mcp_grants_changed&limit=1"` | `grants: ["message"]`; one audit event for `$S` |
| G3 | session token refused | from inside the scratch session, its broker's token on `PUT …/forge-mcp` | `403` |
| G4 | applies at restart | stop and start `$S`, then call the Forge MCP `environment` in it | `grants` includes `message` |
| P1 | preview size | pick an image tool result `T`, then `curl -sD- -o /dev/null "$F/sessions/<id>/tool-result/$T/preview?max_edge=1200"` and the same with `max_edge=2000` | `200` with `X-Preview-Max-Edge: 1200`, then `422` |

If G2's audit read answers 404 on the Guild host, read the audit events on the
embedded Volundr instead. The row is written either way, and the G2 `PUT` itself
proves the write succeeded.

## Switch record (2026-10-30)

The coordinator relaxed the quiet rule. A host could restart when:

- no voice call was live on the agent service (`/api/voice/calls`; a call that
  ended more than 2 minutes ago counts as none);
- ios-coordinator's last turn had ended more than 2 minutes ago;
- on m6, in addition, ios-release was idle and no `qa.sh` process was running.

Sessions marked active were interrupted. The check is `gate.py`.

| | Thor | mini-m6 |
|---|---|---|
| Backup | `~/backups/volundr-pg/nightly-20261030T0556Z` (6.6 GB) | (the B3 off-box copy; m6 data unchanged except one migration) |
| Restart | 08:53:45, ready after 7 s, `CUTOVER_OK` 08:54:01 | stopped its 6 running sessions from 08:56:09; `kickstart -k` 08:57:00, ready after 6 s, `CUTOVER_OK` 08:57:09; the 6 sessions restarted at once, all running and idle |
| Preservation | `PRESERVED` (113 processes, 729 sessions); sample conversation 16/16 turns the same | sessions 15/15; the Thor Guild sees all 15 |
| Ledger | 115 → **116**, `000094` sha `f29350b3…4482c` | 137\|44 → **138\|44**, the same sha; a second migration run added 0 |
| Web | swapped to `forge-grants-ef6dead3` (`X-Niuu-Web-Revision: grants-ef6dead3`; `nginx.conf.bak-20261030-grants`) | served from the release |

**Smoke on Thor** (scratch session `a13c92f6`, deleted afterwards):

- **G1:** `GET` returned 200.
- **G2:** `PUT ["message"]` returned 200, and its audit row is readable through the
  Guild host.
- **G3:** the session's own token got 403 on both `PUT` and `GET`.
- **G4:** after a restart, the new launch's grants are `[message]`.
- **P1:** the preview without `max_edge` was 400. With `max_edge=1200` it was 827,
  the source's own size. 2000 and 319 got 422.
- **S4:** both bad `speak` requests got 422, and nothing was recorded.

S1, S2 and S6 were skipped: each would create a real notification, and the lane's
contract forbids Forge notifications from it.

**Smoke on m6:** the driver's automatic checks, plus the `forge-mcp` route (200) and
a preview with `max_edge` out of bounds (422).
