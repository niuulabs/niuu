# Install Niuu

Use the source path below for the currently verified macOS setup. Both installation
paths lead to the same [first-session quick start](first-local-stack.md).

!!! warning "macOS v1.3.0 cannot start its embedded database"

    The published Apple Silicon binary passes its checksum but fails at startup:
    `initdb` cannot load `libpq.5.dylib`. Use [From source](#from-source) until a
    corrected release passes the bootstrap check. Linux binaries have not been
    verified in this documentation pass. See the [verification record](../operations/quickstart-verification.md).

## One command on a Docker host

On a machine with Docker Engine and the Compose plugin (a DGX Spark, a Linux
box, a Raspberry Pi, a Mac with Docker Desktop), the installer starts the
whole platform as containers. The script is a release asset, published next
to the binaries, and `latest` resolves to the newest release:

```bash
curl -fsSL https://github.com/niuulabs/niuu/releases/latest/download/install.sh | sh
```

To install a specific version, take the script from that release; its copy
pins that release's platform image, and its `checksums.txt` lists it:

```bash
curl -fsSL https://github.com/niuulabs/niuu/releases/download/v1.4.0/install.sh | sh
```

Nothing is compiled or downloaded onto the host but a small `niuu` wrapper in
`~/.local/bin`: the CLI runs from the platform image itself, against the
host's Docker socket, with `~/.niuu` and the data directory mounted at their
host paths. `niuu up`, `niuu status`, `niuu doctor` and `niuu down` all go
through that wrapper. The image tag the wrapper pins is the one the script
pulled (the release's own version; `NIUU_IMAGE_TAG` overrides it), so the CLI
and the platform never drift apart.

The script also writes the initial `~/.niuu/config.yaml`: the vLLM container
image and the models the setup wizard offers to serve locally, with the
`vllm serve` flags their model cards prescribe. That file is yours from then
on; the script never overwrites it. Change a tag or a flag there and run
`niuu up` again. No platform image is rebuilt for it.

It ends by printing a setup URL; open it to finish configuration in the browser.
The script asks nothing in the terminal, needs no root, and never runs
`sudo`. When the host is not ready it stops, explains what it found and
prints the exact command for you to run, then you rerun it: Docker missing,
your user not in the `docker` group, or an NVIDIA GPU that Docker cannot use
yet because no NVIDIA runtime is registered (the fix is one `nvidia-ctk`
command; `NIUU_SKIP_GPU=1` starts without the GPU on purpose). Data lives
under `~/.niuu/data` unless `NIUU_DATA_DIR` says otherwise. `NIUU_NO_UP=1`
installs without starting. See
[Single-host Docker mode](../operations/docker-mode.md) for what runs, where
data lives, and how `niuu up`, `niuu doctor`, and `niuu down` relate to the
`niuu platform` commands.

The same script installs the single-binary CLI for a laptop without Docker:

```bash
curl -fsSL https://github.com/niuulabs/niuu/releases/latest/download/install.sh | sh -s -- --mode mini
```

Between releases, a branch's own script and images are at
`https://raw.githubusercontent.com/niuulabs/niuu/<branch>/scripts/install.sh`
with `NIUU_IMAGE_TAG=<branch name with slashes as dashes>`; that is how a
feature branch is tried on a real host before it ships.

## Release binary: macOS and Linux

The release contains the Niuu executable, web UI, and embedded PostgreSQL.
You do not need to install Python, Node.js, or a separate database to use it.
You still need Git and the agent runtime used by your sessions; the quick start
uses Claude Code.

Run the following in Bash or Zsh. It downloads v1.3.0 to a temporary directory,
checks the published SHA-256 digest, and installs into your user's bin directory.
Choose a different published version from [Releases](https://github.com/niuulabs/niuu/releases)
by changing `VERSION` first.

```bash
VERSION=v1.3.0
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) ARCH=darwin-arm64 ;;
  Linux-x86_64) ARCH=linux-amd64 ;;
  Linux-aarch64|Linux-arm64) ARCH=linux-arm64 ;;
  *) printf 'No release binary for this OS/architecture.\n' >&2; return 1 2>/dev/null || exit 1 ;;
esac

NIUU_DOWNLOAD_DIR=$(mktemp -d)
NIUU_RELEASE_URL="https://github.com/niuulabs/niuu/releases/download/$VERSION"
curl --fail --location "$NIUU_RELEASE_URL/niuu-$ARCH" -o "$NIUU_DOWNLOAD_DIR/niuu-$ARCH"
curl --fail --location "$NIUU_RELEASE_URL/checksums.txt" -o "$NIUU_DOWNLOAD_DIR/checksums.txt"
(
  cd "$NIUU_DOWNLOAD_DIR" || exit 1
  awk -v asset="niuu-$ARCH" '$2 == asset { print }' checksums.txt > selected-checksum.txt
  test -s selected-checksum.txt || exit 1
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c selected-checksum.txt
  else
    shasum -a 256 -c selected-checksum.txt
  fi
) && install -d "$HOME/.local/bin" &&
  install -m 0755 "$NIUU_DOWNLOAD_DIR/niuu-$ARCH" "$HOME/.local/bin/niuu"
```

Expect a checksum result ending in `OK`. If verification fails, stop; do not
install an unverified download. The version, filename, and checksum must come
from the same release.

Add the installation directory to this terminal's path:

```bash
export PATH="$HOME/.local/bin:$PATH"
niuu --version
```

Add that `export` line to your shell startup file (`~/.zshrc` for Zsh or
`~/.bashrc` for Bash) if the directory is not already on your persistent `PATH`.

Now follow [Quick start: your first working session](first-local-stack.md).
It covers Claude authentication, local initialization, launch, and shutdown.

## From source

The contributor path builds the database and web assets, so its first start is
slower than using a release. Install Git, curl, make, a C compiler, pkg-config,
OpenSSL development headers, uv, Node.js, and the pnpm version declared by
`web-next/package.json`. On macOS, use Xcode Command Line Tools and Homebrew;
on Linux, use your distribution's build-tool packages.

```bash
git clone https://github.com/niuulabs/niuu.git
cd niuu
uv sync --python 3.12 --extra dev
./start-dev
```

`start-dev` installs workspace dependencies, builds PostgreSQL and web assets,
and starts a background platform. Use the exact URL it prints. Logs are in
`build/dev-run/logs/platform.log`. Stop this background stack with:

```bash
./stop-dev
```

After stopping the dev stack, expose the source executable in this terminal:

```bash
export PATH="$PWD/.venv/bin:$PATH"
niuu --version
```

Now follow the [quick start](first-local-stack.md) in this terminal, including
authentication, initialization, and foreground startup. The built web and database
assets are reused. Do not run a second platform while `start-dev` still owns the
same port/database.

## Optional: standalone Ravn

The first workspace does not require a separate Ravn installation. To run Ravn
directly, download the matching `ravn-$ARCH` asset from the same release and
verify its entry in `checksums.txt` using the same procedure. Then follow
[Direct and resident assistants](direct-and-resident-assistants.md).
