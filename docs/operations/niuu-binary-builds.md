# Niuu single-executable builds

The distributed executable is `niuu`, with the existing subcommands. PyApp
launches `niuu.__main__:main`, the same entry point as the installed Python
command. Niuu remains Python; only the small PyApp launcher is compiled to native
code. The executable embeds Python, locked application dependencies, the Niuu
wheel, web assets, migrations and PostgreSQL with its extensions.

No system Python or package downloads are needed to bootstrap the executable. The first invocation unpacks the embedded Zstandard archive into
PyApp's per-user installation directory. Subsequent invocations reuse that
installation. PyApp identifies the embedded distribution by its contents, so a
changed payload gets a separate installation. User configuration and databases
continue to use Niuu's existing paths.

External provider tools, including the Claude CLI used by the quick-start
check, retain their existing prerequisites. This does not install provider
credentials or authenticate to a model service.

## Building and running

Install Rust/Cargo, uv and Zstandard on the build host. Linux also needs
`patchelf`. The normal commands are:

```bash
make build                 # web, PostgreSQL, then the executable
make build-cli-bin         # package with web/PostgreSQL assets already prepared
./dist/niuu-darwin-arm64 --version
./dist/niuu-darwin-arm64 platform --help
```

The executable's installed name is `niuu`; artifact names add the target suffix.
The Python wheel's existing service entry points are internal to the embedded
runtime and are not installed as global commands. Sessions launch Skuld through
that bundled Python interpreter.

The old Nuitka builder remains available with
`uv run --extra nuitka python -m cli.build`. It is not the default build path:
native compilation of thousands of Python modules did not meet the build-time
priority for this distribution.

## GitHub Actions

`Niuu binary benchmarks` (`.github/workflows/cli-binaries.yaml`) is manually
dispatched. Routine `dev` pushes do not trigger binary builds. Releases call the
same reusable build workflow.

| Artifact | Native runner |
| --- | --- |
| `niuu-linux-amd64` | `ubuntu-24.04` |
| `niuu-linux-arm64` | `ubuntu-24.04-arm` |
| `niuu-darwin-amd64` | `macos-15-intel` |
| `niuu-darwin-arm64` | `macos-15` |

The web build runs once and feeds all four jobs. pgvector uses portable compiler
flags so it does not require the build runner's particular CPU instruction set. Each job measures packaging,
first CLI launch with an empty installation cache, and three warm launches of
`niuu --version`. It also checks `niuu platform --help` and
`niuu platform up --help`, then runs the existing isolated quick-start check:
real PostgreSQL, HTTP APIs, served web assets, session launch/stop and shutdown.
The platform-to-health measurement uses the warmed launcher installation and
a new database, including initialization and migrations.

Successfully built executables remain downloadable even if a smoke check
fails. Check the overall job result before using them; a failed check blocks
release publication. Benchmark runs receive no provider credentials. The
existing optional release secret `NIUU_QUICKSTART_CLAUDE_TOKEN` enables the
additional live inference check on release builds.

## Where caches are used

| Cache | Contents | Location / invalidation |
| --- | --- | --- |
| uv | Downloaded Python packages | Existing setup action; lockfile, Python and selected extras |
| PostgreSQL | Built database/extension bundle | `build/pginstall`; runner, architecture, version definitions and build script |
| Rust build | Compiled PyApp dependencies | `.build-cache/pyapp-target`; runner, architecture and actual Rust/C/SDK toolchain |
| Cargo downloads | Registry and Git sources | `~/.cargo/registry`, `~/.cargo/git`, saved with the Rust build cache |
| Runtime installation | Unpacked Python and Niuu | PyApp's per-user directory; separate from all CI/build caches |

Each CI run restores the latest matching Rust snapshot and saves an updated
snapshot under a new run/attempt key, including after a failed build. Cargo
validates its locked dependency graph. The PyApp package itself is cleaned and
rebuilt for every payload; only its dependencies are reused. Full executables
and runtime installations are not saved in the CI build cache.

This is what "warm Rust cache" means: it saves compiling the launcher's
third-party Rust dependencies. It does not refer to compiling Niuu or to the
user's startup cache. A cold Rust build still compiles those dependencies once.

## Measurements

The comparison job and each target's summary show build seconds, first/warm CLI
seconds, platform-to-health seconds, exit code and whether a launcher cache was
restored. Build time covers runtime assembly, locked wheel installation,
bytecode generation, resource copying, archive creation and launcher compilation.
It excludes web/PostgreSQL compilation, runner setup, queueing and smoke checks.
Compare Actions job duration separately for total CI cost.

`binary-build-<os>-<arch>` artifacts retain raw JSON, toolchain information and
Cargo's HTML timing report. `quickstart-<os>-<arch>` artifacts retain platform
logs. Build failures retain their measured elapsed time; setup failures have no
build measurement. Reruns replace artifacts with the same name, so download
results first when preserving a before/after comparison.

## Verified CI measurements — 8 November 2026

[Cold Rust build](https://github.com/niuulabs/niuu/actions/runs/42494519494) and
[cached Rust build](https://github.com/niuulabs/niuu/actions/runs/42495843093)
both passed all four targets, including the real quick-start/session checks.
Every target in the second run restored its Rust dependency cache. PostgreSQL
rebuilt once in that run because the portable pgvector flags changed its cache key.

| Target | Cold build | Cached build | First CLI | Warm CLI | Platform healthy |
| --- | ---: | ---: | ---: | ---: | ---: |
| linux-amd64 | 115.24 s | 31.91 s | 6.70 s | 1.37 s | 8.02 s |
| linux-arm64 | 84.08 s | 31.21 s | 4.88 s | 1.39 s | 8.51 s |
| darwin-amd64 | 243.17 s | 139.22 s | 20.22 s | 3.03 s | 25.39 s |
| darwin-arm64 | 90.96 s | 37.78 s | 7.08 s | 1.01 s | 10.96 s |

Startup values are from the second run; installation caches were initially empty.
Platform health includes a new database. Compare within a target: runner hardware differs.
First invocation still pays the one-time extraction cost. The executable includes
the Claude Agent SDK's bundled CLI. Live provider inference was not tested.
