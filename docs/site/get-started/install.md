# Install

Install the small set of tools you actually run.

Most operators start with `niuu`. Install `ravn` when you want to run an
assistant directly or keep a resident assistant running outside the web UI.

## Download `niuu`

Get the latest release from GitHub and replace `VERSION` with the release you
want to install:

```bash
VERSION=v1.0.0
ARCH=darwin-arm64

curl -L "https://github.com/niuulabs/niuu/releases/download/${VERSION}/niuu-${ARCH}" -o niuu
chmod +x niuu
sudo mv niuu /usr/local/bin/niuu
```

Choose the asset that matches your machine:

| Machine | Asset |
| --- | --- |
| macOS Apple Silicon | `niuu-darwin-arm64` |
| Linux x86_64 | `niuu-linux-amd64` |
| Linux ARM64 | `niuu-linux-arm64` |

Verify it:

```bash
niuu --help
niuu --version
```

The release also includes `checksums.txt` if you want to verify the downloaded
binary before moving it onto your `PATH`.

## Initialize the local platform

```bash
niuu platform init
```

The init command writes local platform configuration under `~/.niuu`.

## Optional: install `ravn`

Install `ravn` when you want a direct assistant runtime:

```bash
VERSION=v1.0.0
ARCH=darwin-arm64

curl -L "https://github.com/niuulabs/niuu/releases/download/${VERSION}/ravn-${ARCH}" -o ravn
chmod +x ravn
sudo mv ravn /usr/local/bin/ravn
```

Verify it:

```bash
ravn --help
```

## From source

If you are developing Niuu itself, use the repository scripts instead:

```bash
uv sync --all-extras --dev
pnpm --dir web-next install
./start-dev
```

For normal local operation, prefer the release binary and continue with the
[first local stack](first-local-stack.md).
