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
box, a Raspberry Pi, a Mac with Docker Desktop), the installer downloads the
CLI, verifies its checksum, and starts the whole platform as containers:

```bash
curl -fsSL https://raw.githubusercontent.com/niuulabs/niuu/main/scripts/install.sh | sh
```

It ends by printing a setup URL; open it to finish configuration in the browser.
`NIUU_NO_UP=1` installs without starting. See
[Single-host Docker mode](../operations/docker-mode.md) for what runs, where
data lives, and how `niuu up`, `niuu doctor`, and `niuu down` relate to the
`niuu platform` commands.

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
