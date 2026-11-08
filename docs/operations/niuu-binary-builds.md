# Niuu single-binary builds

`Niuu binary benchmarks` (`.github/workflows/cli-binaries.yaml`) compiles the
existing `cli.build` Nuitka onefile distribution on four native GitHub runners:

| Artifact | Runner |
| --- | --- |
| `niuu-linux-amd64` | `ubuntu-24.04` |
| `niuu-linux-arm64` | `ubuntu-24.04-arm` |
| `niuu-darwin-amd64` | `macos-15-intel` |
| `niuu-darwin-arm64` | `macos-15` |

The workflow runs when build inputs change on `dev`, and can be dispatched
manually from Actions. The implementation branch `codex/nuitka-binaries` also
runs it so the pipeline can be verified before merging. Release builds call
the same reusable workflow. These builds do not gate dev container releases.

The web UI builds once and is downloaded by all four compilation jobs. Each
binary bundles the existing package/resource selection, including migrations,
web assets and embedded PostgreSQL with pgvector. The existing quick-start
test runs against each compiled binary. Successfully compiled binaries remain
available as CI artifacts even if a smoke test fails; check the overall job
result before using them. A failed smoke test blocks release publication.
`quickstart-<os>-<arch>` artifacts preserve the platform startup logs.
An optional release secret, `NIUU_QUICKSTART_CLAUDE_TOKEN`, also enables the
existing live inference check; benchmark runs do not receive that secret.
These are OS-specific binaries, not a universal executable: external runtimes
such as the Claude CLI remain separate prerequisites.

## Reading timings

The `Compilation timing comparison` job summary lists elapsed seconds for each
target, the compilation exit code, whether a compiler cache was restored, and
whether PostgreSQL came from cache. Each build job also has its own timing and
ccache statistics in its summary.

Elapsed time wraps `make build-cli-bin`: Python analysis, generated C compilation,
linking, native dependency collection and onefile compression. It excludes
runner queues, setup, dependency installation, web/PostgreSQL builds and runtime
smoke tests. The Actions job duration includes those additional phases. Native
runner hardware differs, so these are CI measurements, not controlled CPU
benchmarks.

The `compilation-<os>-<arch>` artifacts retain `timing.json`, the toolchain
fingerprint, human-readable and machine-readable ccache statistics, and Nuitka's
XML compilation report. Compilation failures still produce timings and
diagnostics; a failure during setup has no compilation timing.

To compare cold and warm runs, run the workflow once, download its compilation
diagnostics, then rerun all jobs on the same commit after completion. Reruns
replace the artifacts under their existing names. Compare the compilation seconds and ccache
hits/misses. A restored cache alone does not prove useful hits. Run attempts
have distinct immutable cache keys. A first run may already reuse `dev`'s
caches; inspect the recorded cache match before calling it cold.

## Cache behavior

- The existing setup action caches uv downloads keyed by dependencies, Python
  version and selected extras. Dependencies install from the frozen lockfile
  with Python 3.12.
- PostgreSQL bundles are cached separately by runner, architecture, version
  definitions and build script. Application changes do not rebuild PostgreSQL.
- Nuitka's cache directory and ccache objects are restored by runner,
  architecture and toolchain fingerprint. The preferred prefix also includes
  the lockfile and packaging configuration; the toolchain prefix can reuse
  unchanged objects after dependency changes. ccache validates individual
  compiler inputs.
- Compiler caches save under a new run/attempt key, including after a failed
  compilation, so later runs can reuse completed work. Full binaries and
  generated build directories are not cached. Each ccache is limited to 2 GiB;
  GitHub's repository cache quota and eviction policy still apply.

For local builds, use the existing `make build` target. To compile only after
preparing web assets and PostgreSQL, use `make build-cli-bin`.
