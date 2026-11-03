# Promotion runbook: consolidated Forge to Thor, then mini-m6 (LEX-1489)

**Status: APPLIED. Thor 2026-10-27 09:16:44Z (attempt 2), mini-m6 09:46:28Z.**
Thor's attempt 1 (08:46:49Z) rolled back on its own. §7 records what the Thor
switch taught and how a cutover is now launched; §8 is the m6 record.

Prepared 2026-10-26 by the niuu-consolidation
lane. Nothing on Thor `:8080`, Thor's web, mini-m6 or `lexiexp-*` was changed:
live databases were only read (`pg_dump`, `SELECT`), and live files were only
listed and read. Every proof below ran against scratch copies on 127.0.0.1, and
those copies were removed afterwards.

**Who runs it, and when.** One executor runs it top to bottom, Thor first and
then m6, in a quiet window that Damien picks. Each host's switch needs Damien's
explicit go. Run it from a shell that does not live under the Forge API being
restarted (an ssh login or a tmux pane outside Forge). Never run it from a Forge
session on that host. Launch the cutover script itself with
`systemd-run --user --unit <name> --collect` so it has its own cgroup (§7.2).

| | Thor (Home Forge, `:8080`) | mini-m6 (`100.92.69.62:8080`) |
|---|---|---|
| Runs today | `forge-session-metadata-20261016` (11afe41d6, since 2026-10-16) | `forge-m6-6c31bfe1` (09-29 Work release) |
| Promote to | `forge-consolidated-20261026` = `lexi/dev-api-integration` **59c974c8a** | `forge-m6-consolidated-20261026`, same sha |
| Migrations at boot | 20 executed, 12 adopted by alias (§3.1) | 7 Volundr + 1 Ting executed (§3.2) |
| What restarts | the API process only (`KillMode=process`); running sessions survive | the whole LaunchAgent job; **running sessions' brokers die** (§1.3) |
| API downtime | ≈ 30–60 s (stop ≤ 20 s, migrations 1.8 s, boot) | about 1 min, plus the session restarts |
| Rollback target | `forge-session-metadata-20261016` (remove one drop-in) | `forge-m6-6c31bfe1` (restore `forge-run.sh` from its backup: two lines) |
| Point of no return | none (§5) | none (§5) |

---

## 1. Preconditions (both hosts)

### 1.1 Pin the branch

```bash
cd ~/repos/worktrees/niuu-consolidation     # any checkout of xteo/niuu
git fetch origin
git merge-base --is-ancestor 59c974c8afc73cd8af0e9c3065ec32ac170bb3a5 origin/lexi/dev-api-integration && echo on-branch
git diff --stat 59c974c8a origin/lexi/dev-api-integration -- . ':!docs' ':!CONSOLIDATION.md'
#   empty: everything after 59c974c8a is documentation (at prep time, only this runbook, ee08ea5ac)
```

Build the release from **59c974c8a** exactly, not from the branch tip. If the
diff above is not empty, code has landed after the sha that this runbook's dry
runs covered. In that case stop, or re-run §3's dry run on the new tip and update
this document.

Release from committed heads only. The live release directory must be clean, or
the cutover drops someone's uncommitted fix:

```bash
REL_NOW=$(systemctl --user show volundr-forge.service -p WorkingDirectory --value)
git -C "$REL_NOW" status --short            # expect nothing
ssh xteo@mini-m6 'git -C ~/.local/share/niuu/releases/forge-m6-6c31bfe1 status --short'   # expect nothing
```

Both were clean at prep time.

### 1.2 Database backup (LEX-1498), with a proven restore

LEX-1498, the nightly `volundr-pg` dump timer, landed on Thor after its switch
(§7.7). Even so, a window starts with a fresh, one-off backup, taken
immediately before the switch:

**Thor** (`volundr-pg`: Docker, PostgreSQL 16.13). The Home Forge uses the
`volundr` database (70 GB, of which 65 GB is the TOAST of `session_event_log`),
plus `niuu_shared`, `guild`, `observatory`, `ting` and `volundr_forge` (each a
few MB). The `*_work`, `*_notif_candidate` and `volundr_p1_thorcopy` databases
belong to other Forges or to scratch copies; this promotion does not touch them.

```bash
B=~/backups/volundr-pg/pre-b3-$(date -u +%Y%m%dT%H%MZ); mkdir -p "$B"; chmod 700 "$B"
docker exec volundr-pg pg_dumpall -U volundr --globals-only > "$B/globals.sql"   # roles; holds password hashes
for db in niuu_shared guild observatory ting volundr_forge volundr; do
  docker exec volundr-pg pg_dump -U volundr -Fc -Z zstd:3 "$db" > "$B/$db.dump" || echo "DUMP FAILED: $db"
done
chmod 600 "$B"/*; ls -la "$B"
for f in "$B"/*.dump; do docker exec -i volundr-pg pg_restore --list < "$f" > /dev/null && echo "ok $f"; done
```

`pg_dump` is read-only: it takes no locks that block Forge's writes, and Forge
keeps running while it dumps. Take it **before** the quiet window, though; at
this size it is the longest step of the whole promotion.

**Restore drill (done 2026-10-26, prep).** A fresh dump of each database above,
restored into a scratch `postgres:16` (16.13, the same image as `volundr-pg`) on
127.0.0.1:

| Step | Time | Size / result |
|---|---|---|
| `pg_dump -Fc -Z zstd:3 volundr` (live, read-only) | **763 s** (12.7 min; 23:15–23:28 UTC, Forge serving throughout) | 6.39 GB (429 TOC entries) |
| the five small databases + globals | < 1 s each | 4 KB – 220 KB |
| `pg_restore` of `volundr` into an empty database (`-j 8`) | **1513 s** (25 min; the single-threaded `session_event_log` COPY dominates), plus 4 s to copy the file in | exit 0, no errors; 72 GB on disk |
| the five small databases | ≤ 1.2 s each | clean |
| verification | — | sessions 724, ledger 83, notifications 449, chronicles 532 equal to live; `session_event_log` 12,624,107 rows = live minus the 3,562 written between the dump snapshot (23:15) and the live count (23:28) |

Restore procedure (the drill's commands, for a real loss). Do it into a fresh
container, never over a live database:

```bash
docker run -d --name volundr-pg-restore -p 127.0.0.1:55432:5432 \
  -e POSTGRES_USER=volundr -e POSTGRES_PASSWORD=<new> postgres:16 \
  -c max_wal_size=8GB -c maintenance_work_mem=1GB
docker exec volundr-pg-restore psql -U volundr -d postgres -c 'CREATE ROLE ting LOGIN'
for db in niuu_shared guild observatory ting volundr_forge volundr; do
  docker exec volundr-pg-restore psql -U volundr -d postgres -c "CREATE DATABASE \"$db\""
done
# small databases: stream through stdin
docker exec -i volundr-pg-restore pg_restore -U volundr -d guild --exit-on-error < "$B/guild.dump"   # … each
# volundr: copy in, then restore in parallel (-j needs a seekable file)
docker cp "$B/volundr.dump" volundr-pg-restore:/tmp/volundr.dump
docker exec volundr-pg-restore pg_restore -U volundr -d volundr -j 8 --exit-on-error /tmp/volundr.dump
```

`globals.sql` re-creates the `volundr` and `ting` roles together with their
production password hashes. Apply it only when restoring for real, never into a
drill container.

**m6** (embedded PostgreSQL 17.9 under `~/.local/share/niuu-forge/pgdata`). Use
the release's own client over the data dir's socket; the dump lands on Thor, not
on m6, which has 37 GB free (92 % used):

```bash
B=~/backups/mini-m6/pre-b3-$(date -u +%Y%m%dT%H%MZ); mkdir -p "$B"; chmod 700 "$B"
for db in niuu_shared guild observatory ting volundr; do
  ssh xteo@mini-m6 "~/.local/share/niuu/releases/forge-m6-6c31bfe1/build/pginstall/bin/pg_dump \
    -h ~/.local/share/niuu-forge/pgdata -U postgres -Fc $db" > "$B/$db.dump"
done
chmod 600 "$B"/*; ls -la "$B"
```

m6 drill (2026-10-26): `volundr` (7.2 GB) dumped over ssh in **130 s** (1.34 GB).
Restored into a scratch `postgres:17` in **137 s**, giving 32 sessions, 129
ledger rows and 1,735,207 event-log rows. The four small databases took well
under a second each.

### 1.3 The quiet window

**Quiet** means no session on that host is mid-turn: no `running` session whose
`activity_state` is anything but `idle`. Idle running sessions are fine on Thor;
on m6 they are not (see below).

```bash
quiet() {  # $1 = API base, e.g. http://127.0.0.1:8080 or http://100.92.69.62:8080
  curl -s -m 10 "$1/api/v1/forge/sessions?scope=local" | python3 -c '
import sys, json
rows = json.load(sys.stdin)
busy = [r for r in rows if r["status"] == "running" and r.get("activity_state") != "idle"]
idle = [r for r in rows if r["status"] == "running" and r.get("activity_state") == "idle"]
for r in busy: print("BUSY", r["name"], r["activity_state"], r.get("activity_state_since"))
print(f"running: {len(busy)} busy, {len(idle)} idle")
sys.exit(1 if busy else 0)'
}
quiet http://127.0.0.1:8080          # Thor
quiet http://100.92.69.62:8080       # m6 (from Thor)
```

Prep-time snapshot (23:15 UTC): Thor had 14 running sessions (4 `active`, 1
`tool_executing`, 9 `idle`); m6 had 6 running.

**What a running session experiences: Thor.** The unit has `KillMode=process`, so
a restart kills only the API process. Brokers (`python -m skuld`) reparent to
`systemd --user` and keep running. At prep time, 30 production brokers were
alive across four release directories: 23 from `forge-session-metadata-20261016`,
5 from `forge-notification-images-20260930`, and one each from
`forge-questions-20260929-toolimages` and `forge-session-notifications-20260926`.
Those from older release directories have already survived earlier cutovers this
way.

While the API is down:

- The broker's event log buffers its POSTs and retries transient failures
  indefinitely, so no transcript event is lost; the log catches up when the API
  is back.
- The web UI's and the app's WebSockets (`/s/{id}/session`, proxied by the API)
  drop and reconnect.
- Forge-hosted MCP calls (`notify`, `describe_session`) fail for those seconds,
  and the agent sees a tool error.
- Each broker keeps **its own release's code** until that session is restarted.
  B1 features that live in the broker (work facts, `open_controls`, durable
  presented-file upload, the Claude `/model` runtime options) reach a session
  only once it restarts. API-side features (effective model, `/status`,
  descriptions, publications) apply to every session at once.
- Never delete a release directory while a broker still runs from it:
  `ps -eo args | grep 'python -m skuld$'`.

**What a running session experiences: m6.** The LaunchAgent has no
`AbandonProcessGroup`, and every broker shares the job's process group (pgid
77310 at prep time). `launchctl kickstart -k` therefore kills every running
session's broker along with the API. On m6, quiet means **no running sessions
at all**: stop them through the API first, with the owner's agreement, and
restart them after the switch. Claude resumes its conversation on restart.
(§6 proposes `AbandonProcessGroup` so that later m6 promotions behave like
Thor's.)

### 1.3a Busy lanes: never restart a lane mid-work (LEX-1706)

On 2026-10-30 at 20:36 the mini switch stopped every lane while ios-release was running a
release train (build 2442, after its release commit had landed): no result, no tag. Forge
reported the lane `idle`, because Claude ran the train from background shells. Forge's
activity state alone is therefore not enough.

A lane may be restarted (or stopped by a host switch) only when **all** of these hold:

1. Forge reports it `idle` (a question or menu on screen counts as busy);
2. its tmux pane runs no tool command: no shell the agent spawned (`sh|bash|zsh -c …`,
   Claude's Bash tool and background tasks, a Codex exec). MCP servers and other
   long-lived helpers are direct executables and do not count;
3. no train process runs in its pane's process tree: `release.py`, `qa.sh`, `xcodebuild`,
   a journey runner, `archive`, `fastlane`, `altool`, `notarytool`.

Only the pane's process tree counts. A restart or a host switch ends the lane's tmux
server, and with it everything in that tree. launchd or cron jobs (the mini's nightly
`qa.sh`) and other detached processes are not in it and keep running, so they do not
hold a switch. The check lists them as `host_trains`, for the record.

`scripts/forge_lane_busy.py FORGE_BASE TMUX_BIN [SESSION_ID …]` checks this on the host
it runs on, prints JSON (`busy` lanes with their reasons, and `host_trains` for information) and
exits 1 when a lane is busy. Copy it to the host. It runs with the system `python3` (3.9 or later).

```bash
python3 scripts/forge_lane_busy.py http://127.0.0.1:8080 "$(which tmux)"           # Thor
ssh -n xteo@mini-m6 'python3 ~/.local/share/niuu-forge/lane_busy.py \
  http://100.92.69.62:8080 $HOME/.local/share/niuu-forge/tools/bin/tmux'           # m6
```

Each reason names one process, so whoever runs the window can clear it at once:
`pid 601, 04:56:24, polling loop, only waiting: until ! pgrep -f "…"; do sleep 15; done
(now: sleep 15)`.
- **The command** is the one the agent ran, without Claude Code's shell wrapper.
- **"Polling loop, only waiting"** is a loop that does nothing but wait (`until|while …;
  do sleep N; done`). Stopping it loses only that wait.
- **"Polling loop, then runs: …"** carries a follow-up step, for example tagging a release
  once a log shows its result. Stopping it would skip that step, so the gate never ignores
  either kind by itself: the lane, or the operator, decides.
- A shell that only mentions a train (`pgrep -f qa.sh`) is not a train; the train is the
  process that runs it.

On 2026-10-31 two such loops held the mini's window for 40 minutes: a self-matching
`pgrep -f` loop that could never end, and two loops waiting for `RESULT:` in a qa log
that would never come.

How the drivers use it:

- A **host switch that stops lanes** (m6) waits until the check passes twice, 20 s
  apart, and checks again just before the stop. It has a bounded deadline (4 h); past
  it, the host is not switched, and the busy lanes are reported. The release lane is
  never stopped while a train runs.
- A **host switch that leaves lanes running** (Thor's API restart) logs the busy lanes
  when it switches.
- A **session restart** waits, per lane, for the check on that lane, with a bounded
  deadline, and reports the lanes it skipped.

### 1.4 Expected downtime

- **Thor:** the stop takes ≤ 20 s (`TimeoutStopSec=20`, uvicorn's graceful
  shutdown is 15 s), then boot with migrations. In the drill the full migration
  ran in **1.8 s** on a copy of the real 70 GB data (§3.1). The
  previous cutover (10-16) was healthy 4 s after its restart. Expect under a
  minute. **Actual (2026-10-27, attempt 2): 18 s** from `restart` (09:16:44Z) to
  a healthy new build (09:17:02Z); most of it was the old process's graceful
  stop. Attempt 2's `cutover2.sh` fails fast instead of waiting 120 s (§7.4).
- **m6:** pg_ctl stop + start, the migration (0.4 s in the drill) and boot: about
  a minute. The session restarts come on top.

---

## 2. Build the release (Thor)

### 2.1 How today's releases were made (read from the live host)

- **Release worktree:** `git worktree add --detach <sha>` into
  `~/.local/share/niuu/releases/<name>`, from a committed, pushed head.
- **`.venv`:** recent releases symlink an earlier release's venv
  (`forge-session-metadata-20261016/.venv` →
  `forge-notification-signal-20260928/.venv`). That venv holds an editable
  install of the 09-28 tree, but each release's `PYTHONPATH=<rel>/src:<rel>`
  wins, so the code runs from the release. The symlink was safe only because
  `pyproject.toml`/`uv.lock` were unchanged. **59c974c8a changes both**
  (+1372/−902 lines of lock), so this release needs **its own venv**. The live
  venv was built as the project plus `--extra dev`.
- **Unit:** `volundr-forge.service` plus a stack of drop-ins; the
  lexically last one wins. Today that is
  `zzzzzzzzzzzzzzzzzzzz-session-metadata-20261016.conf` (20 z). It sets
  `WorkingDirectory` and an `ExecStart` that runs `/usr/bin/env
  PYTHONPATH=<rel>/src:<rel> PROJECTS__INSTANCE_ID=thor NIUU_BUILD_VERSION=<name>
  <rel>/.venv/bin/python ~/.config/niuu-forge-thor/start_platform.py`.
  `NIUU_CONFIG=~/.config/niuu-forge-thor/config.yaml` comes from the older
  `pi-source.conf` drop-in's `Environment=`, which still applies.
- **Web:** `niuu-forge-web.service` runs nginx with
  `~/.config/niuu-forge-web/nginx.conf` on 127.0.0.1:5173. Tailscale serves it on
  `https://thor.tail737f2a.ts.net:3001`. Its `root` points at a static build under
  `~/.local/share/niuu-forge-web/releases/`, today
  `forge-ux-cleanup-31906a0a-midnight-questions-27b4ff14`. The unit's
  drop-in `WorkingDirectory` names an older build (`…-uxstyles-ae19f223`); only
  `root` in nginx.conf matters. Each build carries Thor's hand-written
  `config.json` (`demoMode: false`, theme `midnight-blue`, every service on
  `/api/v1/...`). A swap edits `root` plus the `X-Niuu-Web-Revision` header,
  then runs `systemctl --user reload niuu-forge-web.service`.
- **Guards:** the rollout directory pattern
  `~/.local/share/niuu/rollouts/<name>/` holds `cutover.sh` (writes the drop-in,
  stops the health-check timer, restarts the API, waits up to 120 s for
  `/health` to report the new build, rolls back otherwise),
  `preserve_check.py` (a snapshot/compare of brokers, sessions,
  conversation hashes and config hashes), backups and a `STATUS.md`.

### 2.2 Build (proven 2026-10-26 in `/tmp/lex1489/scratch-releases/`, never started)

```bash
NAME=forge-consolidated-20261026
REL=~/.local/share/niuu/releases/$NAME
git -C ~/repos/niuu fetch origin
git -C ~/repos/niuu worktree add --detach "$REL" 59c974c8afc73cd8af0e9c3065ec32ac170bb3a5
cd "$REL"
env -i PATH=$HOME/.local/bin:/usr/bin:/bin HOME=$HOME UV_PYTHON=3.13 uv sync --frozen --extra dev
# web (Node 22.22.1 + pnpm 9.12.0, as Thor has them)
cd "$REL/web-next"
env -i PATH=$HOME/.local/share/pnpm:$HOME/.nvm/versions/node/v22.22.1/bin:$HOME/.local/bin:/usr/bin:/bin \
    HOME=$HOME PNPM_HOME=$HOME/.local/share/pnpm sh -c 'pnpm install --frozen-lockfile && pnpm build'
```

Use `env -i` with an allow-list. A shell on this box carries a live session's
`SKULD__*`, `PYTHONPATH` and production `DATABASE__*` variables.

**Every release carries its web app.** The API serves it from the release's own
`web-next/apps/niuu/dist`. When `web-next` is unchanged since the previous release, the
build may be skipped, but the dist must still be placed. Staging ends with the check, and
the pre-GO preflight runs it again (on the mini, the same test of `dist/index.html` and a
non-empty `dist/assets` over ssh):

```bash
python3 scripts/forge_release_dist.py place "$REL" --previous ~/.local/share/niuu/releases/<previous> \
    --health-url http://127.0.0.1:8080/health          # add --built after a pnpm build here
python3 scripts/forge_release_dist.py check "$REL"      # exit 1: do not write GO
```

`place` copies the previous release's dist only when `web-next` is unchanged between the two
commits; if it changed, it fails and asks for the build. It keeps an existing dist only with
`--built`, and refuses a release with uncommitted `web-next` changes.

**Tooling is never exercised against the release a host is serving.** Try a script on a
scratch copy or on a staged, non-serving release. `place` enforces this, with no override:
it refuses a target that `/health` names as the serving build, or that one of this user's
running processes has as its working directory, and it refuses everything when `/health` cannot be read. On
2026-10-31 a test run of `place` replaced the web dist of Thor's serving release (with
identical files); that is why the guard exists. Promotion ten (2026-10-31) was
staged with an empty dist; the smoke's `web /` check caught it (404) and the cutover rolled
back. That smoke check stays as the last line of defence.

Drill results: `uv sync --frozen --extra dev` took 0.5 s (warm uv cache) and gave
CPython 3.13.13 with the editable install pointing at the release's own `src`.
`pnpm install && pnpm build` took 32 s, producing 28 MB in
`web-next/apps/niuu/dist`. These checks also passed:

```bash
cd "$REL"; env -i PATH=/usr/bin:/bin HOME=/tmp NIUU_BUILD_VERSION=$NAME .venv/bin/python -I -c "
import sys; sys.path[:0]=['src','.']
from niuu.build_identity import build_identity; print(build_identity())"
# {'revision': '59c974c8afc73cd8af0e9c3065ec32ac170bb3a5', 'build': 'forge-consolidated-20261026',
#  'source_sha256': '0ac8adc5…', 'dirty': False}      <- what /health will report
```

The same check confirmed that Thor's `start_platform.py` still fits the new
code. It imports `niuu.app.RootServer` and `cli.registry.PluginRegistry`
(15 plugins discovered), plus `bootstrap_database`, `bootstrap_sql_for_service`
and `database_name_for_service` from `niuu.service_databases`. `RootServer`
still has `_build_app`, `_host`, `_port`, `_server` and `_task`.

### 2.3 Why Thor keeps its own start script

`start_platform.py` replaces `RootServer.start` and does four things the
upstream start would not:

1. It keeps the external Docker Postgres. Upstream's
   `NIUU_DATABASE_MODE=external` path would also run the **`ting/` migration
   stream** against Thor's `volundr` database. Thor's Ting tables predate the
   Ting ledger (0 `ting/` rows), so that stream fails at
   `ting/000025_legacy_schema_compat` (`column "depends_on" does not exist`;
   B0 dry run).
2. It provisions `niuu_shared`/`guild`/`observatory` itself.
3. It binds the tailnet address into `NIUU_SERVER_HOST`/`VOLUNDR__URL`.
4. It pins the Claude models.

With the script, only Volundr's own startup migrations run (`volundr/main.py
::_bootstrap_startup_schema`), which is exactly what the dry run in §3.1
exercised.

Two upstream start behaviours the script does not reproduce, and what to do
about each:

- `NIUU_SERVER_PUBLIC_HOST` is not set, so `server_public_host` falls back to
  `NIUU_SERVER_HOST` (100.66.123.128). This is unchanged from today, except that
  `host_identity.tailnet_name` would inherit the raw IP. §2.4 therefore sets
  `tailnet_name` explicitly.
- `install_uvicorn_log_redaction()` is not called (the script logs at `info`
  with access logs on). The last two hours of the live journal held no
  credential-shaped query strings. Adding the one call to the script is a
  follow-up, not part of this window, because `preserve_check.py` hashes the
  script as a stable file.

### 2.4 Configuration (`~/.config/niuu-forge-thor/config.yaml`)

Append these lines. The other keys stay as they are; secrets in that file stay
where they are:

```yaml
# LEX-1489 (B3): durable presented files, artifact publications, host identity.
presented_files:
  enabled: true
  public_base_url: http://thor.tail737f2a.ts.net:8080
  store:
    adapter: volundr.adapters.outbound.local_presented_files.LocalPresentedFileStore
    root: /home/thor/.niuu/presented-files
artifacts:
  enabled: true          # the default; stated so the file says what runs
host_identity:
  host: thor
  tailnet_name: thor.tail737f2a.ts.net
```

Back the file up first (`cp -p config.yaml config.yaml.bak-20261026-b3`). Both the
API and every broker started afterwards read it (`NIUU_CONFIG`).

Validated (prep): the new code's `volundr.config.Settings` loaded a scratch copy
of today's file plus these lines. It returned `presented_files.enabled=True`,
the store above and `host_identity(host='thor',
tailnet_name='thor.tail737f2a.ts.net')`. `validate_public_base_url` accepts the
URL: it must be a `*.ts.net` name or a Tailscale address, and anything else is
refused at start. The API listens on `0.0.0.0:8080`, so the tailnet name reaches
it. `/home/thor/.niuu/presented-files` is on the system disk (1.3 TB free),
outside every release directory, so later promotions keep it.

The chart values `presentedFiles`/`artifacts`/`hostIdentity` are the Helm
spellings of these keys. Neither Thor nor m6 is deployed by the chart.

### 2.5 Web

```bash
WEB=~/.local/share/niuu-forge-web/releases/forge-consolidated-20261026-59c974c8
LIVE_WEB=$(awk '$1=="root"{gsub(";","",$2); print $2; exit}' ~/.config/niuu-forge-web/nginx.conf)
mkdir -p "$WEB" && rsync -a "$REL/web-next/apps/niuu/dist/" "$WEB/"
cp "$LIVE_WEB/config.json" "$WEB/config.json"            # Thor's live config, not the build's demo one
cp "$LIVE_WEB/config.live.json" "$WEB/config.live.json"
python3 -c "import json; d=json.load(open('$WEB/config.json')); assert d['demoMode'] is False; print(d['theme'])"
```

Carry Thor's `config.json` over unchanged:

- The build's own `config.json` is `demoMode: true` with mock services.
- The new app's plugin list (`login home realms setup volundr ting ravn mimir
  valkyrie observatory bifrost guild settings logout`) is identical to 31906a0a's.
  A plugin missing from `config.plugins` loads (`enabled !== false`), so the
  surface stays what it is today.
- The live file satisfies the new schema: no mock services, every http service
  has a `baseUrl`, and the theme `midnight-blue` is in the enum and is the new
  default.

The swap is done in §4.1 step 6, after the API is healthy.

---

## 3. Migrations

Both hosts migrate **at boot**, inside the API process, under the advisory lock
`volundr.startup-schema`. Each file runs in its own transaction together with its
ledger row (`volundr_schema_history`, keyed by filename + sha256).
`migrations/lineage-aliases.json` lets a renumbered file adopt an
already-applied row with identical SQL instead of re-running it. There is no
separate migrate command.

### 3.1 Thor

**Dry run (2026-10-26):** the release tree's own runner on a **full restore of
the fresh dump** (real data, not only the schema), Volundr stream only, as
`start_platform.py` boots. A second run of the new runner followed, then the old
release's runner and the rollback probe (§5).

- ledger **83 → 115**: **20 executed, 12 adopted by alias** (SQL not run; the
  ledger row was added because an identical file is already applied under its old
  name). This is B0's result (17 upstream files + our 3 B1 files executed; B1a's
  correction moved 000050–000052 from "adopted" to "already applied").
- **executed:** `000062_pat_authority`, `000063_topology_source_authority`,
  `000064_instance_tenant_attribution`, `000065_admin_settings`,
  `000066_compute_leases`, `000067_compute_pools`,
  `000068_compute_profile_revision`, `000076_chronicle_attribution`,
  `000077_instance_health`, `000078_session_span_w3c_trace_id`,
  `000079_resident_realm_binding`, `000080_session_participants`,
  `000081_ravn_trigger_store`, `000082_ravn_budget_ledger`,
  `000083_guild_node_join`, `000087_session_actor`,
  `000088_workload_delegations`, `000091_session_work_facts`,
  `000092_session_usage_status`, `000093_forge_artifact_publications`.
- **adopted:** `000069`–`000075` (session turn start, delivery claims, the
  duplicate event-log index, the ledger table itself, projects, coordination
  revision, read state), `000084`–`000086` (notifications, delivery-rate index,
  reads), **`000089_session_descriptions` ← 000087** and
  **`000090_forge_notification_images` ← 000072**.
- **Time on the real data: 1.4 s for all 93 files, 1.8 s wall** including the
  service-DB bootstrap (niuu-shared 30, guild 28, observatory 1 statements).
  Among the executed files, 000092 adds two columns and a partial index to
  `session_event_log` (12.6 M rows), and 000076 backfills 532 chronicles.
- Second run: no-op (115). Then the 10-16 release's runner on the result: a
  no-op as well (115), and its bootstrap ran cleanly.

**Pre-checks** (read-only, immediately before the switch; each must give the
stated answer):

```bash
q() { docker exec volundr-pg psql -U volundr -d volundr -Atc "$1"; }
q "SELECT count(*), max(filename) FROM volundr_schema_history"
#   83|000087_session_descriptions.up.sql
q "SELECT count(*) FROM volundr_schema_history WHERE filename LIKE 'ting/%'"
#   0
q "SELECT sha256 FROM volundr_schema_history WHERE filename IN
   ('000072_forge_notification_images.up.sql','000087_session_descriptions.up.sql') ORDER BY filename"
#   cd39abfca6355c64df73f10a1b5ae9c0933cc8cfebf43a6962993a2b2e5565e4
#   3d47e0afee0c560b32bc66ae6d9857cd277806564ae748d55917d77b5ba99b88   (the alias targets)
q "SELECT count(*) FROM niuu_instances WHERE NOT (
     (visibility='system' AND owner_id IS NULL)
  OR (visibility='tenant' AND owner_id IS NULL AND tenant_id IS NOT NULL)
  OR (visibility='user' AND owner_id IS NOT NULL))"
#   0   (000064's new CHECK)
q "SELECT pid, now()-xact_start, left(query,60) FROM pg_stat_activity
   WHERE datname='volundr' AND xact_start < now()-interval '1 minute' AND pid<>pg_backend_pid()"
#   nothing: no long transaction may hold `sessions`/`session_event_log` (the
#   ALTERs take a brief ACCESS EXCLUSIVE lock and no lock_timeout is set)
df -h / | tail -1     # the dump plus room for growth
```

**Stop condition.** Do not switch if any pre-check differs: a different ledger
count or last row, any `ting/` row, an alias sha mismatch, a CHECK violation, or
a long transaction.

At boot, a migration that fails leaves the API unhealthy. `cutover.sh` then
rolls back after 120 s, and the failed file's transaction leaves no partial
state. Afterwards, compare `volundr_schema_history` with the dry-run list above.
**Any file executed or adopted that is not on that list means the code differs
from what was rehearsed: roll back.**

Post-check:

```bash
q "SELECT count(*) FROM volundr_schema_history"            # 115
journalctl --user -u volundr-forge.service --since -10min | grep -c 'Applied startup migration'   # 32 (20 executed + 12 adopted; both log this line)
```

### 3.2 m6

**Dry run (2026-10-26):** a full restore of a fresh dump into PostgreSQL 17, then
the release's runner as `niuu platform up` runs it (`RootServer._run_migrations`:
Volundr + `ting/` streams, then the niuu-shared/guild/observatory bootstrap):

- ledger **129 → 137**; nothing adopted (m6 never applied our old numbers).
- executed: `000087_session_actor`, `000088_workload_delegations`,
  `000089_session_descriptions`, `000090_forge_notification_images`,
  `000091_session_work_facts`, `000092_session_usage_status`,
  `000093_forge_artifact_publications`, `ting/000047_a2a_campaign_launch_slug`
  (B0 counted "4 + 1" before B1a/B1b added 000091–000093).
- wall time 0.4 s; the bootstrap ran 30 + 28 + 1 statements.
- second run: no-op (137); the 09-29 release's runner on the migrated copy: no-op.

Pre-checks:

```bash
mq() { ssh xteo@mini-m6 "~/.local/share/niuu/releases/forge-m6-6c31bfe1/build/pginstall/bin/psql \
         -h ~/.local/share/niuu-forge/pgdata -U postgres -d volundr -Atc \"$1\""; }
mq "SELECT count(*), count(*) FILTER (WHERE filename LIKE 'ting/%') FROM volundr_schema_history"   # 129|43
mq "SELECT max(filename) FROM volundr_schema_history WHERE filename NOT LIKE 'ting/%'"             # 000086_…
```

Stop condition: as on Thor. Post-check: 137 rows, 44 of them `ting/`.

---

## 4. Switch

### 4.1 Thor

```bash
NAME=forge-consolidated-20261026
RO=~/.local/share/niuu/rollouts/$NAME; mkdir -p "$RO/backups"; cd "$RO"
cp -p ~/.local/share/niuu/rollouts/forge-session-metadata-20261016/{cutover.sh,preserve_check.py,smoke.sh} .
```

1. **Backup** (§1.2) into `$RO/backups/` or `~/backups/volundr-pg/pre-b3-…`, then
   the pre-checks (§3.1).
2. **Config:** back up and append (§2.4). Running brokers do not re-read it.
3. **Quiet:** `quiet http://127.0.0.1:8080` must exit 0, and Damien says go.
4. **Snapshot, cutover, compare:**

   ```bash
   python3 preserve_check.py snapshot before.json 0a713405-79b2-566c-a953-802e3a080c49
   ./cutover.sh $NAME \
     ~/.config/systemd/user/volundr-forge.service.d/zzzzzzzzzzzzzzzzzzzzz-consolidated-20261026.conf \
     forge-session-metadata-20261016 2>&1 | tee cutover.log            # expect CUTOVER_OK
   python3 preserve_check.py snapshot after.json 0a713405-79b2-566c-a953-802e3a080c49
   python3 preserve_check.py compare before.json after.json            # must print PRESERVED
   ```

   - The drop-in has **21 z's**, so it sorts after today's 20-z one.
   - `cutover.sh` writes the drop-in with `PROJECTS__INSTANCE_ID=thor` and
     `NIUU_BUILD_VERSION=$NAME`, then stops `volundr-forge-healthcheck.timer`, so
     the minute health check cannot restart the unit mid-boot.
   - It restarts only the API and waits up to 120 s for
     `/health.build == $NAME`, then re-arms the timer.
   - If the build never becomes healthy, it removes the drop-in, restarts the
     old build and prints `ROLLED_BACK`.

   Three differences the compare can report that are not losses:

   - The old 10-16 compare expected identical session statuses. A session that
     changes state on its own during the window (it finishes a turn) shows as a
     status change, so re-check any reported difference by hand.
   - The stable-file hash of `config.yaml` **will** differ, because step 2
     changed it. That one difference is expected.
   - The conversation sample is compared by hash, and the conversation
     response may have gained fields between 11afe41d6 and 59c974c8a. Keep the
     bodies so that a hash difference can be told apart from a loss. Run the
     first command right after the `before` snapshot and the second right after
     the `after` snapshot:

     ```bash
     curl -s 127.0.0.1:8080/api/v1/forge/sessions/0a713405-79b2-566c-a953-802e3a080c49/conversation > conv-before.json
     curl -s 127.0.0.1:8080/api/v1/forge/sessions/0a713405-79b2-566c-a953-802e3a080c49/conversation > conv-after.json
     python3 - <<'PY'
     import json
     b, a = (json.load(open(f)) for f in ("conv-before.json", "conv-after.json"))
     tb, ta = (x.get("turns", x) if isinstance(x, dict) else x for x in (b, a))
     same = len(tb) == len(ta) and all(
         p.get("id") == q.get("id") and p.get("content") == q.get("content") for p, q in zip(tb, ta))
     print(len(tb), len(ta), "SAME TURNS" if same else "TURNS DIFFER")
     PY
     ```

     `SAME TURNS` with a different hash means the shape changed, not the
     conversation.

   Anything else the compare reports is a loss: roll back (§5).

5. **Smoke** (§4.3).
6. **Web swap:**

   ```bash
   cp -p ~/.config/niuu-forge-web/nginx.conf ~/.config/niuu-forge-web/nginx.conf.bak-20261026-b3
   OLD_ROOT=$(awk '$1=="root"{gsub(";","",$2); print $2; exit}' ~/.config/niuu-forge-web/nginx.conf)
   OLD_REV=$(basename "$OLD_ROOT" | sed 's/^forge-ux-cleanup-//')
   sed -i "s|$OLD_ROOT|$WEB|; s|$OLD_REV|consolidated-59c974c8|g" ~/.config/niuu-forge-web/nginx.conf
   /usr/sbin/nginx -t -c ~/.config/niuu-forge-web/nginx.conf
   systemctl --user reload niuu-forge-web.service
   curl -sI http://127.0.0.1:5173/volundr/forge | grep -i x-niuu-web-revision   # consolidated-59c974c8
   ```

   A reload keeps the WebSockets that nginx is proxying open. Browsers pick up
   the new bundle on their next page load (`index.html` is `no-store`).

7. Write `$RO/STATUS.md`: times, `cutover.log`, compare output and smoke results.

### 4.2 m6

Prepare the release on m6 the day before (outside the window); none of this
touches the running Forge. m6 has no Node and no Homebrew. uv 0.12.13 and Python
3.13 live under `~/.local/share/niuu-forge/`.

```bash
# on Thor: build the web once (§2.2) and keep its dist
ssh xteo@mini-m6 '
  set -e
  R0=~/.local/share/niuu/releases/forge-m6-6c31bfe1
  R=~/.local/share/niuu/releases/forge-m6-consolidated-20261026
  git -C $R0 fetch origin lexi/dev-api-integration          # partial (blob:none) clone; works over https
  git -C $R0 worktree add --detach $R 59c974c8afc73cd8af0e9c3065ec32ac170bb3a5
  mkdir -p $R/build && ln -s ../../forge-m6-6c31bfe1/build/pginstall $R/build/pginstall   # same PG 17.9 binaries
  cd $R && PATH=~/.local/share/niuu-forge/tools/bin:/usr/bin:/bin \
    UV_PYTHON=~/.local/share/niuu-forge/python/cpython-3.13-macos-aarch64-none/bin/python3 \
    uv sync --frozen --extra dev'
rsync -a "$REL/web-next/apps/niuu/dist/" \
  xteo@mini-m6:.local/share/niuu/releases/forge-m6-consolidated-20261026/web-next/apps/niuu/dist/
```

- **Venv:** a symlink to the 09-29 venv will not do, because the lock changed.
  Every package that has no macOS wheel (`nkeys`, `nuitka`, `patchelf`,
  `pywin32`, `nemo-switchyard`) is in the 09-29 lock at the same version, and
  that lock built on m6. Nothing new needs a compiler.
- **pginstall:** the symlink is what the 10-16 staging (`forge-m6-1895f5fc`) did.
- **Web:** m6 serves the web from the API (`web-ui` + `runtime-config` mounts:
  `cli.resources.web_dist_dir()` picks `web-next/apps/niuu/dist` in the release).
  `/config.json` is rendered from the build's own `config.live.json`, all http,
  so no hand-written file is needed. The visible change is the default theme
  (today `xteo`, then `midnight-blue`). The 10-16 staging never received a web
  dist, which is one more reason never to switch to it.
- **Check:** with `PYTHONPATH=$R/src:$R` and `NIUU_BUILD_VERSION=forge-m6-consolidated-20261026`,
  the release's python prints `build_identity()` with `revision 59c974c8…`,
  `dirty False`.
- **Mounts:** every entry of `forge-run.sh`'s `--mounts` list exists in the new
  code (checked against `niuu/app.py`).

**Config** (`~/.config/niuu-forge/config.yaml`; back it up first):

```yaml
# LEX-1489 (B3): durable presented files, artifact publications, host identity.
presented_files:
  enabled: true
  public_base_url: http://mini-m6.tail737f2a.ts.net:8080
  store:
    adapter: volundr.adapters.outbound.local_presented_files.LocalPresentedFileStore
    root: /Users/xteo/.niuu/presented-files
artifacts:
  enabled: true
host_identity:
  host: mini-m6
  tailnet_name: mini-m6.tail737f2a.ts.net
```

Validated against the new `Settings` from a scratch copy of today's file.
`forge-run.sh` already exports `PROJECTS__INSTANCE_ID=mini-m6`, so `host` would
have resolved to `mini-m6` anyway (B1b.7.4 called it unknown); it is set
explicitly anyway. m6's disk has 37 GB free: watch `~/.niuu/presented-files`
(§6.1).

**Switch** (the window: no running sessions, §1.3). With the owner's agreement,
stop m6's running sessions through the API, and record their ids so they can be
started again after the switch:

```bash
M=http://100.92.69.62:8080/api/v1/forge
curl -s "$M/sessions?scope=local" | python3 -c '
import sys, json
for r in json.load(sys.stdin):
    if r["status"] == "running": print(r["id"], r["name"])' | tee m6-running-before.txt
cut -d" " -f1 m6-running-before.txt | while read id; do curl -s -X POST "$M/sessions/$id/stop" -o /dev/null -w "%{http_code} $id\n"; done
quiet http://100.92.69.62:8080      # 0 busy, 0 idle
```

Then switch:

```bash
ssh xteo@mini-m6 '
  set -e
  S=~/.local/share/niuu-forge
  cp -p $S/forge-run.sh $S/forge-run.sh.bak-20261026-b3
  sed -i "" -e "s|releases/forge-m6-6c31bfe1|releases/forge-m6-consolidated-20261026|" \
            -e "s|NIUU_BUILD_VERSION=forge-m6-6c31bfe1|NIUU_BUILD_VERSION=forge-m6-consolidated-20261026|" $S/forge-run.sh
  diff $S/forge-run.sh.bak-20261026-b3 $S/forge-run.sh || true    # exactly lines 5 (R=) and 12 (NIUU_BUILD_VERSION)
  launchctl kickstart -k gui/$(id -u)/world.niuu.forge.m6'
for i in $(seq 1 120); do curl -s -m 3 http://100.92.69.62:8080/health | grep -q '"build":"forge-m6-consolidated-20261026"' && break; sleep 1; done
curl -s http://100.92.69.62:8080/health
ssh xteo@mini-m6 'tail -50 ~/Library/Logs/niuu-forge/forge.log | grep -E "Applied startup migration|ERROR"'
```

**Lessons from Thor applied to m6 (§7).** m6 kills every session anyway, so
the persistent-process versus tmux distinction does not matter there, but the
rest does:

- Run the switch from Thor, not from a session on m6, and launch it with
  `systemd-run --user --unit lexi-b3-m6 --collect <script>` on Thor, so the
  driver outlives whatever happens to the calling session.
- Readiness fails fast: stop waiting as soon as `/health` names a
  `failed_plugins` entry or `forge.log` shows `Failed to start … lifespan` /
  `Failed to create API app`, then roll back (§5.2). The script writes a
  one-line verdict file, as Thor's `attempt2/RESULT` did.
- The two Thor failures should not occur on m6, but check both during the
  window prep: m6's seeded Guild instance is `embedded://local-forge`, which the
  transport check exempts. m6's running release (6c31bfe1) already uses the
  file-backed Ting repo and boots through the same workflow-catalog check, and
  m6 has its `ting/` ledger (44 rows). Rehearse the switch on a copy of m6's
  data first, as Thor's attempt 2 was, rather than relying on this reading.
- After m6 switches, list its registry (`GET http://100.92.69.62:8080/api/v1/niuu/instances`),
  including its records of Thor and Spark.
  Give every `http://` row `allow_plaintext: true`, plus `health_path: /health`
  for any host that has not upgraded yet (§7.5 item 3). Then add both checks,
  every instance `ok` and the federated session count, to the smoke.
- m6 has no health-check job: its only LaunchAgent is `world.niuu.forge.m6`
  (checked 2026-10-27), so there is nothing to pause.

After the smoke (§4.3), start the sessions that were stopped:

```bash
cut -d" " -f1 m6-running-before.txt | while read id; do curl -s -X POST "$M/sessions/$id/start" -o /dev/null -w "%{http_code} $id\n"; done
```

The launcher stops the embedded Postgres cleanly before it starts the new
release (`pg_ctl stop -m fast`). That Postgres runs from `NIUU_PG_BIN_DIR`, which
is the same 17.9 binaries through the symlink.

### 4.3 Smoke (each host; Thor first, m6 after its switch)

`A` = the API base (`http://127.0.0.1:8080` on Thor, `http://100.92.69.62:8080`
for m6), and `F=$A/api/v1/forge`.

| # | Check | Command / action | Expect |
|---|---|---|---|
| 1 | build | `curl -s $A/health` | `revision` 59c974c8afc7…, `build` the release name, `dirty: false`, `failed_plugins: []` |
| 2 | sessions list | `curl -s "$F/sessions?scope=local" \| python3 -c 'import sys,json; r=json.load(sys.stdin); print(len(r), sorted(r[0])[:5])'` | same count as before (Thor 60 at prep time); every row has `model`, `short_description`, `description`; Thor: `preserve_check compare` → PRESERVED |
| 3 | conversation read | `curl -s "$F/sessions/0a713405-79b2-566c-a953-802e3a080c49/conversation" \| head -c 300` and `…/conversation?tail=5` | 200 with turns; the tail read returns the last 5 turns |
| 4 | describe | Thor: `./smoke.sh` (10-16 script, stopped session) | `SMOKE_OK` |
| 5 | instance | `curl -s $F/instance` | `host` thor / mini-m6, `tailnet_name` the `*.ts.net` name |
| 6 | status | `curl -s $F/sessions/<a running session>/status` | 200 with `context`, `last_turn`, `totals`, `compactions`, `model`, `engine`, `host`. Usage and context fill in as new events arrive: the projection runs at ingest and does not backfill history, so a session that has not replied since the switch shows empty counts |
| 7 | scratch session | create one Claude session in a scratch folder (UI, or `create_session` through the Forge MCP; see `docs/forge/openclaw-session-orchestrator-guide.md`) | starts on the new broker code |
| 8 | `describe_session` | in it: `forge-describe --short "B3 smoke"` | the session row shows it; one `session_updated` |
| 9 | `notify` | in it: the Forge MCP `notify` (kind `info`, title "B3 smoke") | listed by `curl -s "$F/notifications?limit=5"`. It **reaches Damien's phone** under his rules, so say so beforehand |
| 10 | presented file | in it: `present-file ./b3-smoke.png`. The path must be under the session's workspace or home: the broker refuses anything else, `/tmp` included (`path is outside the session roots`; on macOS `/tmp` is `/private/tmp`). That rule predates B3 | the notice URL is `http://<host>.tail737f2a.ts.net:8080/api/v1/forge/sessions/<id>/files/presented/pf_…`; `curl -s -D - -o /dev/null <url>` → 200 (use GET: the route answers HEAD with 405), `cache-control: private, max-age=86400, immutable`, `content-security-policy: sandbox`; `~/.niuu/presented-files/<id>/` exists; **stop** the session and fetch it again → still 200 (durable) |
| 11 | `/model` choices | while it runs, idle: `curl -s $A/s/<id>/api/runtime-options` | `source: "catalog"`, `claude-*` models, `current.model` = launch model; then `curl -s -X POST $A/s/<id>/api/runtime-options -H 'content-type: application/json' -d '{"options":{"model":"<another id>"}}'` → 200, and `GET $F/sessions/<id>` shows that `model`. It also writes the CLI's saved default (§6.5): set it back afterwards |
| 12 | web | Thor: `https://thor.tail737f2a.ts.net:3001` (m6: its `:8080`) | header `X-Niuu-Web-Revision: consolidated-59c974c8` (Thor); the UI in midnight blue; Settings offers midnight/deep/dark blue and light; m6 sessions show under mini-m6 through the Guild |
| 13 | cleanup | delete the scratch session | 204; `~/.niuu/presented-files/<id>/` is gone |

Any red in rows 1–6 means rolling back (§5). For rows 7–13, judge whether the
red is the feature (record it and keep the release) or the release (roll back).

---

## 5. Rollback

### 5.1 Thor

```bash
rm ~/.config/systemd/user/volundr-forge.service.d/zzzzzzzzzzzzzzzzzzzzz-consolidated-20261026.conf
systemctl --user daemon-reload && systemctl --user restart volundr-forge.service
curl -s 127.0.0.1:8080/health      # build forge-session-metadata-20261016, revision 11afe41d…
# web, if swapped:
cp -p ~/.config/niuu-forge-web/nginx.conf.bak-20261026-b3 ~/.config/niuu-forge-web/nginx.conf
systemctl --user reload niuu-forge-web.service
# config: optional. The old build ignores the new keys (Settings has extra="ignore").
# The attempt-2 edits (§7.5) have their own backup, taken just before them:
cp -p ~/.config/niuu-forge-thor/config.yaml.bak-20261027-b3a2 ~/.config/niuu-forge-thor/config.yaml
```

Removing the drop-in also drops the Ting-off launcher (`start_platform_b3.py`):
the old release comes back through the unchanged `start_platform.py` with Ting
on. Proven on a scratch copy before the switch: the old release on the migrated
schema with the new config reports `/health` ok, `failed_plugins: []`.

### 5.2 m6

```bash
ssh xteo@mini-m6 'cp -p ~/.local/share/niuu-forge/forge-run.sh.bak-20261026-b3 ~/.local/share/niuu-forge/forge-run.sh
                  launchctl kickstart -k gui/$(id -u)/world.niuu.forge.m6'
```

(This again kills m6's running brokers.)

### 5.3 What happens to the database: no point of no return

The new columns, tables and ledger rows stay. The old releases were verified to
tolerate them in four ways:

1. **By running their code against the migrated copies.**
   - On Thor's full data copy, after the new migrations, the 10-16 release's
     runner (`forge-session-metadata-20261016`) was a no-op: 115 rows before and after, and its own bootstrap (24 + 9 + 1 statements) ran cleanly.
   - On m6's copy, the 09-29 runner (`6c31bfe1`, both streams) was a no-op:
     137 rows.
   - Each old runner only looks up its own files by name. The extra rows
     (000088–000093, the alias adoptions, `ting/000047`) are ignored.
   - The old service-DB bootstrap is `CREATE … IF NOT EXISTS` and ran cleanly.
2. **By a repository probe** (`rollback_probe.py`, appendix): the old release's
   own Postgres adapters mapped every session row, got, created, updated (and,
   on Thor, described) a probe session, appended and read event-log rows, listed
   notifications for every session, and deleted the probe.
   - Thor (10-16 code): 724 sessions mapped, `update_description` ok, event-log append/read ok, 356 notifications mapped, all steps ok. The same probe with the **new** code on the migrated copy also passed
   - m6 (09-29 code): 32 sessions mapped, 236 notifications mapped, all steps
     ok.
3. **By reading the schema changes** that execute (§3) for anything an older
   writer could trip on:
   - Every new `NOT NULL` column has a default (`open_tool_count DEFAULT 0`,
     `health DEFAULT 'unknown'`, `tenant_id/owner_id DEFAULT ''`).
   - No column the old code uses is dropped or renamed.
   - 000064 replaces `niuu_instances_scope_check` with a strictly weaker check:
     the `user` case no longer requires `tenant_id IS NULL`, so every row the
     old check accepted, the new one accepts.
   - 000083's unique indexes are partial (`WHERE node_id IS NOT NULL`) or on
     new tables. The old code never sets `node_id`.
   - The new CHECKs sit only on new tables (images, publications, participants,
     compute leases).
4. **By reading the old adapters' row mapping:** columns are named explicitly
   (`row["x"]`, no `**row` into strict models), so extra columns are ignored.

What a rollback does lose, none of which corrupts anything:

- **New data goes unread.** Effective model, usage, work facts, publications and
  presented-file metadata stop being served. Rows written meanwhile stay.
- **Usage gaps.** Event-log rows the old code appends carry no `usage_key`. If
  the release is re-promoted, usage for those turns is not projected
  retroactively, so the `/status` usage of sessions active during the rollback
  stays incomplete until their next replies.
- **Presented files.** Durable files stay under `~/.niuu/presented-files/`; the
  old API has no route for them, so their URLs answer 404 until the release is
  back.

Dropping the new schema is never required. If it is ever wanted, run the `.down.sql`
files of the executed list in reverse order, then delete those ledger rows.
Never drop the alias-adopted rows: their SQL predates this promotion.

---

## 6. Open questions B3 must answer first (from `CONSOLIDATION.md`)

Each has a recommendation; none blocks the switch unless Damien says so.

1. **Durable presented files: retention and quota** (B1b.7.1). No TTL and no
   total quota; files are removed only with their session's delete.
   *Recommendation:* keep "until delete" for Thor (1.3 TB free). m6 has 37 GB
   free on a 92 %-full disk, so before enabling it there:
   - add a `du -sh ~/.niuu/presented-files` line to the m6 checks;
   - schedule a follow-up for a total-size quota that refuses new copies loudly
     (per `no-hidden-limits.md`), rather than a silent TTL.
2. **Publications after a session delete** (B1b.7.3). They outlive the session,
   and a `present_file` publication's URL then answers 404.
   *Recommendation:* keep them (they are the record that something was
   published), but have the publication read mark `session_deleted: true`, so the
   app greys the link instead of opening a 404. Small follow-up, not B3.
3. **Conversation `tail` reads bypass `DurableTranscripts`** (`CONSOLIDATION.md` §3.2/§7.1).
   *Recommendation:* leave it for B3; reads are bounded (≤ 50k frames). Route
   tail through the single-flight cache only if the app polls `tail` on stopped
   sessions concurrently. The phone's pattern decides; measure after B3.
4. **`activity_heartbeat.stale_output_seconds` is not rendered by the Skuld
   template** (B1a.6.3). *Recommendation:* no action for B3. Thor and m6 are
   local-process Forges, so brokers read `NIUU_CONFIG` and the template does not
   apply; the default (600 s) stands. Add it to the chart's Skuld template with
   the next chart change, for Kubernetes Forges.
5. **`/model` writes the CLI's saved default** (B1d.6.1). In a native local
   session without its own `CLAUDE_CONFIG_DIR`, a model switch (typed, slash
   passthrough or the app picker) rewrites the box user's
   `~/.claude/settings.json`, so new sessions inherit it.
   *Recommendation:* accept it for B3, because new Forge sessions pass their
   model explicitly (`--model`), so the saved default only affects a bare
   `claude` on the box. Tell Damien. Follow up with a per-session config dir only
   if a bare `claude` on Thor/m6 matters. Smoke row 11 sets it back.
6. **Resident asks** (B1c.6.3). Ravn's `notify` tool does not advertise
   `question`/`ask`. *Recommendation:* out of B3; a Ravn decision, and it changes
   nothing on either host.

Also from the summary, decided here:

- **Thor's start script:** keep it (§2.3).
- **Thor's web:** rebuild from the branch (§2.5).
- **m6:** promote from 6c31bfe1 directly. Never switch to the staged
  `forge-m6-1895f5fc`, which has no web dist and an older base.
- **Public image (`forge/ux-*`):** unrelated to B3.
- **m6 `AbandonProcessGroup`:** add it to the plist in a later window (`bootout`
  + `bootstrap`; that itself kills the brokers once), so m6's next promotions
  keep running sessions alive like Thor's. *Recommendation:* do it in this same
  m6 window, since the sessions are stopped anyway. It is a plist change, so
  Damien's call.

---

## 7. Thor switch record and lessons (2026-10-27)

The full record lives in `~/.local/share/niuu/rollouts/forge-consolidated-20261026/`:
`cutover.log` and `candidate-failure.log` (attempt 1); `attempt2/PLAN.md`,
`cutover2.sh`, `cutover2.log`, `RESULT`, `before2.json`/`after2.json` (attempt 2).

### 7.1 Timeline (UTC)

| Time | Event |
|---|---|
| 08:33–08:46 | `pre-b3-20261027T0833Z` dump (§1.2), volundr 763 s |
| 08:46:49 | attempt 1: `cutover.sh` restarts the API. The executing session (a persistent-process session inside the service) is killed with it |
| 08:47:08 | candidate up; 08:47:09–11 Volundr 000062–000093 applied |
| 08:47:09 | **ting** lifespan fails: workflow-catalog check |
| 08:47:11 | **guild** lifespan fails: plaintext transport refused; `/api/v1/forge/*` 404, `/health` 503 `degraded` |
| 08:49:08 | 120 s readiness wait expires; rollback; old build ready in 4 s. Forge API unusable ≈ 2 min 20 s; nothing lost, the old release runs on the new schema |
| 08:55 | health-check timer paused by the coordinator |
| 09:00–09:16 | diagnosis and rehearsal on a scratch restore of the 08:33 dump |
| 09:16:40 | config edits applied (§7.5), Ting-off launcher installed |
| 09:16:43 | attempt 2 launched as `systemd-run --user --unit lexi-b3-cutover-2 --collect …/attempt2/cutover2.sh` |
| 09:16:44 | API restart |
| 09:17:02 | `forge-consolidated-20261026` healthy, `failed_plugins: []` (**downtime ≈ 18 s**) |
| 09:17:13 | autonomous smoke passed → `RESULT: CUTOVER_OK` |
| 09:18 | health-check timer re-enabled by the coordinator |

`preserve_check compare`: 724/724 sessions, 101 protected processes, conversation
sample identical (16/16 turns). Its one reported status change (`lexi-mini`,
stopped → running) was an explicit `POST /start` at 09:18:04, after the switch.

### 7.2 Launch the cutover outside the service, and park persistent-process sessions

`KillMode=process` protects tmux-hosted sessions and Skuld brokers, which
survive the restart. It does **not** protect a persistent-process session: that
session lives inside the API's own cgroup, so the restart kills it, and with it
any shell it started. `setsid nohup` does not help, because the cgroup is what
gets killed. Therefore:

- Park (stop) persistent-process sessions before the window, or accept that
  they restart.
- Launch the cutover script with `systemd-run --user --unit <name> --collect`,
  never as a child of a session. Make it fully autonomous: switch, readiness,
  smoke and rollback, then a one-line verdict file (`RESULT`) and a complete
  log. Whoever started it may not be alive to report.
- Write the exact script and the config diff (secrets masked) into the log
  *before* launching, so the record is complete even if the caller dies.

### 7.3 The health-check timer

`volundr-forge-healthcheck.timer` restarts an unhealthy unit. Keep it stopped
from before the restart until the smoke has passed, then re-enable it. A
cutover script must not re-arm it on its own; attempt 1's `cutover.sh` did so
after rolling back. Attempt 2's script never touches it.

### 7.4 Reading `/health`, and the fail-fast readiness rule

`/health` 503 with `"status": "degraded"` does not mean "still starting". It
means a plugin's app or lifespan failed. uvicorn still reports "Application
startup complete", and the failed plugin's routes are missing (404) or answer
503. The body names the plugins: read `failed_plugins`.

`cutover2.sh` polls once a second and stops waiting as soon as one of these is
true:

- `/health.build` is the new release and `failed_plugins` is non-empty;
- the unit's journal since the restart cursor contains `Failed to start … lifespan`
  or `Failed to create API app`.

It succeeds on `status: ok` with an empty `failed_plugins` and the new build.
It also gives up after 60 s (the boot took 2–5 s in rehearsal and 4 s live).
Then it runs the smoke list (sessions list, the ios-coordinator session
present, a conversation read, `/status` on two sessions, the web UI, `/health`).
On any failure it removes the drop-in, restarts the old build and waits for it
under the same rules.

### 7.5 What changed on Thor to make it boot

1. **Guild `allow_plaintext: true`** on the seeded "Thor" instance
   (`niuu.instances[0].config` in `~/.config/niuu-forge-thor/config.yaml`).
   This release refuses `http://` for a non-loopback, non-`embedded://`
   instance unless the instance opts in. Thor's is `http://100.66.123.128:8080`
   on the tailnet, an already-encrypted path, which is the opt-in's stated
   case. Without the opt-in the Guild lifespan fails, and Guild mounts
   `/api/v1/forge` and `/api/v1/forge/sessions`: hence attempt 1's 404s.
2. **Ting off on Home.** The new release's launcher is
   `~/.config/niuu-forge-thor/start_platform_b3.py`. It is `start_platform.py`
   plus `registry.apply_config({"ting": False})`, and only the new drop-in uses
   it. Why:
   - This release defaults Ting to the file-backed workflow repo and refuses
     to start while PostgreSQL `workflows` holds rows without a verified
     migration marker (`ting.main._assert_workflow_catalog_migrated`).
   - The remedy it names, `python -m ting.migrate_workflows`, cannot run on
     Thor. The release's Postgres reader fails on `tenant_id`, because Thor's
     Ting tables stop around `ting/000032` (no ledger, §2.3). The tables from
     000033–000047 (`workflow_executions`, `delivery_executions`,
     `workflow_waits`, `workflow_versions`, `tracker_connections`) do not exist
     at all.
   - Ting on Home was dormant: 1 saga and 1 draft workflow (both
     2026-05-22), 0 raids, 0 campaigns, 7 packaged system workflows. Its rows
     stay untouched in PostgreSQL.
3. **Every remote instance needs the same opt-in, and the old releases need a
   health path.** After the switch, `GET /api/v1/niuu/instances` showed mini-m6
   and Spark `unreachable`, and the federated session list fell from 76 to
   Thor's 61. There were two causes:
   - their registry rows (`http://100.92.69.62:8080`, `http://100.127.141.74:8080`)
     hit the same plaintext refusal;
   - once that was fixed, the new probe path for `volundr` instances,
     `/api/v1/forge/health`, does not exist on their older releases (404).

   Both rows live in the Guild registry, not the config seed, so they were fixed
   live, with no restart, at about 09:30. `PATCH` replaces `config` whole, so
   resend the existing keys:

   ```bash
   B=127.0.0.1:8080/api/v1/niuu/instances
   curl -s -X PATCH $B/<id> -H 'content-type: application/json' \
     -d '{"config":{"defaultFolder":"<existing>","allow_plaintext":true,"health_path":"/health"}}'
   curl -s -X POST $B/<id>/test        # {"ok":true,...}; health flips on the next 30 s sweep
   ```

   Result: all three `ok`; the federated list is 76 again (Thor 61, mini-m6 14,
   Spark 1). The two fixes are independent:
   - `allow_plaintext` alone restores federation, because the session proxy
     dials `base_url`.
   - The `health` label stays `unreachable` until `health_path` is set too,
     because the probe keeps getting a 404. That is not reconcile lag.
   - "Federation works but the host shows unreachable" therefore means the
     probe path, not the network. `health_path: /health` stays valid after those hosts upgrade.
   **Rule:** every `http://` tailnet instance, seeded or registered, on every
   host running this release, needs `allow_plaintext: true`. Check
   `GET …/instances` right after any switch: `health` must be `ok` for every
   reachable host. This includes m6's own records once m6 runs this release.
4. **`workflow_repository.kwargs.catalog_path: ~/.niuu/workflows-home`**, which
   is inert while Ting is off. `~/.niuu/workflows` is **Thor Work's live
   catalog** (`forge-work-*`, same HOME, same default path). Home must never
   point at it.

### 7.6 Bringing Thor's Ting back (not done; needs its own rehearsal)

1. Seed a `ting/` ledger that matches Thor's real Ting schema (it diverges from
   the files at 000025, `column "depends_on" does not exist`). Then apply the
   rest up to 000047 on a scratch restore until the stream is clean.
2. Run the workflow migration with the **app's** database identity. Run bare,
   the module uses Ting's defaults (`ting`/`ting`), which is the wrong catalog:

   ```bash
   env DATABASE__NAME=volundr DATABASE__USER=volundr DATABASE__PASSWORD=volundr \
     $REL/.venv/bin/python -m ting.migrate_workflows --catalog-path ~/.niuu/workflows-home   # dry run
   # … --apply             writes YAML + .migration-complete.yaml there; never writes PostgreSQL
   # … --skip-if-migrated  exits 0 when the marker is current
   ```

   Rolling it back means deleting `~/.niuu/workflows-home`.
3. Remove the `apply_config` line (or the `_b3` launcher) and restart.
4. Every start of the *old* release re-upserts the packaged workflows, which
   moves `max(updated_at)`. A rollback therefore stales the marker, and the new
   release then refuses to start until `--apply` is run again.

### 7.7 Standing backup (LEX-1498)

Since 2026-10-27, `volundr-pg-backup.timer` (02:30 UTC daily, `Persistent=true`)
runs `~/.local/bin/volundr-pg-backup`. It writes per-DB `pg_dump -Fc -Z zstd:3`
dumps and globals for `volundr volundr_forge guild ting niuu_shared observatory`
into `~/backups/volundr-pg/nightly-<stamp>/`, and verifies each dump with
`pg_restore --list`. It keeps 14 days. Its freshness marker is
`~/backups/volundr-pg/LATEST.json`, the last successful dump; `LAST_RUN.json`
records the last attempt, failed or not. Restore as in §1.2. Hand-made dumps
(`pre-b3-*`) are never rotated.

Off-box: `volundr-pg-offbox.timer` (Sunday 04:30 UTC) runs
`~/.local/bin/volundr-pg-offbox`. It copies the dump named by `LATEST.json`, and
refuses if that dump is not `ok` or is more than 36 h old. The copy goes to
`spark:/shared/backups/thor-volundr-pg/<stamp>/`, and the job keeps the 2 newest
copies there. Each copy is checked file by file with sha256 and carries its own
`SHA256SUMS`. Markers: `OFFBOX.json` and `OFFBOX_LAST_RUN.json`. To restore
after losing Thor, `rsync` the directory back, run `sha256sum -c SHA256SUMS`,
then restore as in §1.2. The first copy (6.38 GB) took 32 s.

## 8. mini-m6 switch record (2026-10-27)

Record: `~/.local/share/niuu/rollouts/forge-consolidated-20261026/m6/` on Thor
(`cutover-m6.sh`, `cutover-m6.log`, `RESULT`, `ROWS_DONE`, `second_run.py`).
Damien's go: "Mini go, don't think anything running. Take the recommendations."

### 8.1 Timeline (UTC)

| Time | Event |
|---|---|
| 09:35–09:39 | backup `~/backups/mini-m6/pre-b3-20261027T0935Z` on Thor: 1.35 GB in 247 s; all 5 dumps pass `pg_restore --list` |
| 09:40–09:43 | release `forge-m6-consolidated-20261026` built on m6 (486 MB; `59c974c8a`, `dirty: false`, same `source_sha256` as Thor); config extended (backup `config.yaml.bak-20261027-b3`); plist and launcher staged in `~/.local/share/niuu-forge/b3-staged/` |
| 09:46:20 | the coordinator stops m6's six running sessions |
| 09:46:26 | `systemd-run --user --unit lexi-b3-m6 --collect …/m6/cutover-m6.sh` **on Thor**; preflight: 0 running, 14 sessions, ledger `129\|43` |
| 09:46:28 | `bootout` of the 6c31bfe1 job; launcher and plist (`AbandonProcessGroup`) installed; `bootstrap` |
| 09:46:34 | new build healthy, `failed_plugins: []`: **downtime ≈ 6 s** |
| 09:46:35 | ledger `137\|44` (Volundr 000087–000093 + `ting/000047`); a second runner pass added 0 |
| 09:46:36 | automatic smoke passed → `RESULT: CUTOVER_OK` |
| 09:47:27 | the coordinator restarts the six sessions |
| 09:49–09:51 | interactive rows on a scratch session (after a slot was freed, §8.3); scratch session and folder deleted |

### 8.2 How it differed from the plan

- **Driver on Thor.** `cutover-m6.sh` runs under `systemd-run` on Thor and acts
  on m6 over ssh, so nothing on m6 can kill it. The rules are the same as
  Thor's (§7.4): fail fast on `failed_plugins` or a lifespan error in
  `forge.log`, a verdict file, and automatic rollback (old launcher and old
  plist, `bootstrap`, wait for 6c31bfe1).
- **The switch is `bootout` + `bootstrap`, not `kickstart -k`,** because the
  plist changed in the same step (§6, `AbandonProcessGroup`).
- **Ting is not loaded on m6.** None of its route domains are in `forge-run.sh`'s
  `--mounts`, so its catalog check cannot trip (m6's `workflows` table is
  empty anyway). The `ting/` migration stream still runs at boot.
- **Guild:** m6's registry has only its own `embedded://local-forge` record,
  which the plaintext rule exempts. It holds no records of Thor or Spark, so
  there was nothing to patch.
- **No health-check job on m6**, so nothing to pause.

### 8.3 Smoke

Automatic, in the driver: sessions list 14/14, a conversation read and
`/status` on `919384a8`, web `/` 200, `/instance` (host `mini-m6`, tailnet
`mini-m6.tail737f2a.ts.net`), `/health` ok, and Thor's Guild facade showing all
14 m6 sessions.

By hand:
- `/status` and the `/model` choices on a running session (read only:
  `source: catalog`, six Claude models).
- A scratch session: `describe_session`, one `notify`, and a presented-file
  round trip, all green. m6 has no delivery rules or sinks, so the `notify`
  went to the feed only (`deliveries: []`). The file was still 200 after
  the session stopped, and its directory went with the session's delete.

**`max_concurrent` caps smoke.** m6 runs `pod_manager.max_concurrent: 6`. Once
its six sessions were restarted, the scratch session was refused (`No session
slot is free`), and one idle session had to be stopped for it. Run the
interactive rows *before* restarting the sessions, or keep a slot free.

### 8.4 Does an m6 restart still kill sessions?

- **Before 2026-10-27:** yes. Without `AbandonProcessGroup`, launchd killed the
  whole job process group, brokers included.
- **Now (`AbandonProcessGroup: true`):** launchd no longer kills the group when
  the API exits.
  - A plain `launchctl kickstart -k gui/$(id -u)/world.niuu.forge.m6` (for
    example, to raise `max_concurrent`) is *expected* to behave like Thor's
    `KillMode=process`: the API restarts, and brokers keep running and
    reconnect. The API code that leaves brokers alive on shutdown is the same
    code that does so on Thor.
  - **This is not yet observed on m6.** Prove it at the next restart with one
    idle scratch session first.
  - Until then, treat an m6 restart as session-killing, as before.
- A plist change still needs `bootout` + `bootstrap`. Treat that as
  session-killing too.

So "persistent-process sessions die with the service" (§7.2) holds on both hosts.
"m6 kills every session on restart" becomes Thor-like once the `kickstart -k`
proof above passes.

### 8.5 Follow-ups

- Presented files: `presented_files` is on, with no total quota (the release has
  only the per-file `max_bytes`). Watch `du -sh ~/.niuu/presented-files` on m6
  (8 KB after the smoke). Follow-up ticket: a total-size quota that refuses new
  copies loudly.

---

## Appendix A: drill tools

The drill ran with two scripts, kept here so B3 can re-run them if the branch or
a ledger moves. Both refuse anything but 127.0.0.1. Run them under `env -i`
with the release's python and `-I`.

`drive_migrations.py <release> <thor|m6> 127.0.0.1 <port> <user> <password>`
applies a release's migrations the way that host boots and prints the executed
and adopted ledger rows:

- **thor:** the Volundr stream, then the service-DB bootstrap that
  `start_platform.py` runs.
- **m6:** `RootServer._run_migrations`, i.e. Volundr plus `ting/`, then the
  bootstrap.

`rollback_probe.py <old-release> 127.0.0.1 <port> <user> <password> volundr`
runs the probe of §5.3.

```python
"""LEX-1489 drill: migrate a SCRATCH copy the way the release boots.

Usage: python -I drive_migrations.py <release-root> <mode thor|m6> <host> <port> <user> <password>

thor: Volundr stream only (start_platform.py skips RootServer._run_migrations; the Volundr
      plugin's _bootstrap_startup_schema runs), then the niuu-shared/guild/observatory
      bootstrap that start_platform.py's _provision_service_dbs runs.
m6:   RootServer._run_migrations: Volundr + ting/ streams, then the same bootstrap.
Prints ledger rows added (executed vs adopted-by-alias) and wall time per step.
Refuses anything but 127.0.0.1.
"""

import asyncio
import json
import logging
import sys
import time

release, mode, host, port, user, password = sys.argv[1:7]
if host != "127.0.0.1":
    raise SystemExit("scratch only: host must be 127.0.0.1")
sys.path[:0] = [f"{release}/src", release]

import asyncpg  # noqa: E402

from cli.resources import migration_dir, ordered_migration_files  # noqa: E402
from niuu.adapters.postgres_schema import apply_startup_migrations  # noqa: E402
from niuu.service_databases import (  # noqa: E402
    bootstrap_database,
    bootstrap_sql_for_service,
    database_name_for_service,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")


async def ledger(conn):
    rows = await conn.fetch("SELECT filename, sha256 FROM volundr_schema_history")
    return {r["filename"]: r["sha256"] for r in rows}


async def main():
    conn = await asyncpg.connect(host=host, port=int(port), user=user, password=password,
                                 database=database_name_for_service("volundr"))
    try:
        streams = ["volundr"] if mode == "thor" else ["volundr", "ting"]
        before = await ledger(conn)
        for variant in streams:
            files = ordered_migration_files(migration_dir(variant))
            t0 = time.monotonic()
            if variant == "ting":
                await apply_startup_migrations(conn, files, namespace="ting")
            else:
                await apply_startup_migrations(conn, files)
            print(f"[{variant}] {len(files)} files verified in {time.monotonic() - t0:.1f}s")
        after = await ledger(conn)
    finally:
        await conn.close()
    added = sorted(set(after) - set(before))
    aliases = json.loads((migration_dir("volundr") / "lineage-aliases.json").read_text())
    adopted = [f for f in added
               if any(before.get(a["filename"]) == a["sha256"] for a in aliases.get(f, []))]
    executed = [f for f in added if f not in adopted]
    print(f"ledger {len(before)} -> {len(after)}; executed {len(executed)}, adopted {len(adopted)}")
    for f in executed:
        print("  executed", f)
    for f in adopted:
        print("  adopted ", f)
    for svc in ("niuu-shared", "guild", "observatory"):
        sql = bootstrap_sql_for_service(svc)
        if sql:
            t0 = time.monotonic()
            await bootstrap_database(host=host, port=int(port), user=user, password=password,
                                     database=database_name_for_service(svc), statements=sql)
            print(f"[bootstrap] {svc}: {len(sql)} statements in {time.monotonic() - t0:.1f}s")


asyncio.run(main())
```

```python
"""LEX-1489 drill: does an OLD release's code still work on the MIGRATED scratch schema?

Usage: python -I rollback_probe.py <old-release-root> <host> <port> <user> <password> <db>

Exercises the old release's own Postgres adapters (the paths a rolled-back Forge uses
most): map every session row, get, create a probe session, update it, set its
description (when the old code has it), append + read event-log rows, list notifications
for a real session, then delete the probe. Refuses anything but 127.0.0.1.
"""

import asyncio
import sys
from uuid import uuid4

release, host, port, user, password, db = sys.argv[1:7]
if host != "127.0.0.1":
    raise SystemExit("scratch only: host must be 127.0.0.1")
sys.path[:0] = [f"{release}/src", release]

import asyncpg  # noqa: E402

from volundr.adapters.outbound.pg_session_event_log import PostgresSessionEventLog  # noqa: E402
from volundr.adapters.outbound.postgres import PostgresSessionRepository  # noqa: E402
from volundr.adapters.outbound.postgres_notifications import (  # noqa: E402
    PostgresNotificationRepository,
)


async def main():
    pool = await asyncpg.create_pool(host=host, port=int(port), user=user, password=password,
                                     database=db, min_size=1, max_size=4)
    try:
        sessions = PostgresSessionRepository(pool)
        rows = await sessions.list()
        print(f"list: mapped {len(rows)} sessions")
        newest = max(rows, key=lambda s: s.created_at)
        assert (await sessions.get(newest.id)).id == newest.id
        print(f"get: {newest.id} ok")

        probe = newest.model_copy(update={"id": uuid4(), "name": "lex1489-rollback-probe"})
        await sessions.create(probe)
        probe = probe.model_copy(update={"message_count": probe.message_count + 1})
        await sessions.update(probe)
        if hasattr(sessions, "update_description"):
            from datetime import UTC, datetime

            await sessions.update_description(probe.id, {"short_description": "probe"},
                                              datetime.now(UTC))
            print("update_description: ok")
        got = await sessions.get(probe.id)
        assert got is not None and got.message_count == probe.message_count
        print("create/update/get probe: ok")

        log = PostgresSessionEventLog(pool)
        source = None
        for s in sorted(rows, key=lambda s: s.created_at, reverse=True):
            entries = await log.read_after(s.id, 0, limit=3)
            if entries:
                source = entries
                break
        if source:
            import dataclasses

            copies = [dataclasses.replace(e, session_id=probe.id, seq=i + 1)
                      for i, e in enumerate(source)]
            await log.append(copies)
            back = await log.read_after(probe.id, 0, limit=10)
            assert len(back) == len(copies)
            print(f"event log append+read: {len(back)} rows ok, latest_seq {await log.latest_seq(probe.id)}")

        notes = PostgresNotificationRepository(pool)
        n = 0
        for s in rows:
            n += len(await notes.list_for_session(s.id, after=0, limit=50))
        print(f"notifications list_for_session over all sessions: {n} rows mapped")

        await pool.execute("DELETE FROM session_event_log WHERE session_id = $1", probe.id)
        assert await sessions.delete(probe.id)
        print("delete probe: ok")
        print("ROLLBACK_PROBE_OK")
    finally:
        await pool.close()


asyncio.run(main())
```

The scratch containers (`lex1489-drill-pg16`, `lex1489-drill-pg17`) and their
volumes, the dumps, config copies and scratch worktrees under `/tmp/lex1489/`
were removed after the drill.
