# How the quick start is verified

The [quick start](../get-started/first-local-stack.md) has executable command
blocks. `scripts/check_quickstart.py` reads those blocks directly; it does not
maintain a second copy of the startup commands.

## Every release binary

The release workflow runs this check after building each supported Niuu binary,
before uploading that binary for publication. A failure prevents the GitHub
Release job from completing.

```bash
uv run python scripts/check_quickstart.py --niuu dist/niuu-darwin-arm64
```

Use `niuu-linux-amd64` or `niuu-linux-arm64` for those release targets. For a
source checkout with PostgreSQL and web assets already built:

```bash
uv run python scripts/check_quickstart.py --niuu .venv/bin/niuu
```

The test installs nothing itself. It requires Git, curl, Bash, and a real Claude
CLI. The release workflow installs a pinned Claude CLI, currently 2.1.181.
It creates temporary configuration, database, state, and workspace paths and
selects unused loopback ports. It preserves the user's home and authentication. Mímir also uses a temporary store,
so the knowledge example never writes to the user's normal knowledge base.

It checks:

- the guide's version and initialization commands;
- real embedded PostgreSQL startup, migrations, and `/health`;
- delivery of the web UI and the documented Claude runtime definition;
- the HTTP examples for model catalogs, providers, service discovery, and sessions;
- real Mímir source ingestion and readback from the knowledge guide;
- creation of a blank session through Forge and its running state;
- stopping that session and shutting down the foreground platform with SIGINT
  (the signal sent by Ctrl+C).

This check makes no model request. It is a runtime/bootstrap check, not proof of
provider access or an agent's successful work. It does not click the browser UI.

## Live provider check

With a working Claude subscription login, add `--live`:

```bash
uv run python scripts/check_quickstart.py --niuu .venv/bin/niuu --live
```

This first executes the guide's real authentication probe, then launches the
session with the exact tutorial prompt. It checks that the agent creates
`hello.txt` with the exact expected bytes, and that the file remains after
stopping the session. Missing or invalid authentication fails the test; there
is no fake provider response. This uses the provider's quota.

In GitHub Actions, configure the repository secret
`NIUU_QUICKSTART_CLAUDE_TOKEN` with a valid Claude Code OAuth token for a dedicated
test account. The release workflow passes it as `CLAUDE_CODE_OAUTH_TOKEN`. When
the secret exists, a live-check failure blocks that binary's release. When it
is absent, the workflow explicitly reports **Live inference NOT TESTED** in its
summary. The infrastructure check remains mandatory.

The secret has not been provisioned by this documentation change. Expired tokens
must be renewed by the account owner. Keep credentials out of test artifacts.

## Verification record

On 2026-09-11, the source checkout based on `c457eb59` with the accompanying
quick-start changes passed the bootstrap/session smoke test on macOS ARM64 with
Python 3.12.12 and Claude Code 2.1.181. The browser launch steps were exercised
against a fresh database and captured in the guide's screenshots.

The initial live request failed with an expired local Claude OAuth token, despite
`claude auth status` reporting a login. This is why the guide requires a real
request before proceeding. Until a successful rerun is recorded, the final file
creation is **not verified** for this checkout. Linux release binaries and the
new GitHub Actions steps must also run in CI before claiming those platforms
passed.

The published `v1.3.0` macOS ARM64 asset was also downloaded and its published
SHA-256 verified. Startup failed because bundled PostgreSQL could not load
`niuu/pginstall/lib/libpq.5.dylib`. The current build includes PostgreSQL through
Nuitka's data-directory option; [Nuitka requires explicit DLL configuration](https://nuitka.net/user-documentation/nuitka-package-config.html)
for native shared libraries. The build now explicitly includes PostgreSQL native files and repairs Nuitka's
macOS dependency paths before one-file compression. A compiled PostgreSQL probe
passed initialization, startup, SQL execution, and shutdown on macOS ARM64. The
published v1.3.0 asset is still defective; a complete corrected Niuu release must
pass the binary smoke gate before publication. A source bootstrap pass alone
does not validate release packaging.

## Full documentation rewrite checks

The rewritten service guides were exercised against a fresh isolated source host
on macOS ARM64. Model/catalog/health reads, instance and target discovery, session
reads, and Mímir ingestion/readback passed. Raw source ingestion returned no
synthesized pages, and knowledge search returned an empty result; the guide now
explains that distinction. Ting workflow reads returned HTTP 401 without caller
authentication, as documented.

All Bash blocks also passed shell-syntax checks, including the generated CLI
reference. This checks parsing only, not successful execution against a provider
or deployment. The release docs job repeats this check.

The source umbrella chart passed dependency resolution, Helm lint, and rendering
in a temporary copy. No cluster deployment was performed. The strict site build
passed after generating the Völundr OpenAPI file. The landing page was inspected
in the browser; existing onboarding URLs remain available as forwarding pages.

## Maintaining the guide

When the launch UI changes, repeat Source → Runtime → Confirm manually and
refresh the screenshots from the real app. When CLI flags or startup behavior
change, update the marked command blocks and rerun the smoke test. The published
site also has a strict build gate on release.

A green site build checks rendering and internal links; a green bootstrap test
checks startup and session lifecycle; only the live test verifies model work.
Keep those results distinct when reporting release readiness.

## Reproduce the native packaging check

With PostgreSQL already built under `build/pginstall`, compile the small test
entry point using the same native inclusion and layout hooks as the Niuu build:

```bash
uv run --with 'nuitka[onefile]==4.1.3' python -m nuitka --onefile \
  --output-dir=build/pg-probe --output-filename=postgres-probe \
  --user-package-configuration-file=src/cli/postgres.nuitka-package.config.yml \
  --user-plugin=scripts/postgres_nuitka_plugin.py \
  --include-data-dir=build/pginstall=niuu/pginstall \
  '--noinclude-data-files=niuu/pginstall/bin/*' \
  tests/packaging/postgres_probe.py
build/pg-probe/postgres-probe
```

This test uses a temporary database and Unix socket without a TCP listener. It
also exercises a dynamically loaded text-search module. It does not substitute
for the full Niuu binary smoke test in the release workflow.
