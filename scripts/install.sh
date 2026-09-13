#!/bin/sh
# Niuu installer — the one command for a clean machine.
#
#   curl -fsSL https://get.niuu.ai | sh
#   curl -fsSL https://get.niuu.ai | sh -s -- --mode mini
#
# Modes:
#   docker (default)  The whole platform as containers on this Docker host.
#                     Nothing is installed but a small `niuu` wrapper that runs
#                     the CLI from the platform image against the host's Docker
#                     socket, so the host needs Docker Engine + Compose only.
#   mini              The single-binary CLI with its embedded database, for a
#                     laptop without Docker. Downloads the release binary.
#
# Environment:
#   NIUU_IMAGE_TAG     platform image tag for docker mode (default: latest)
#   NIUU_DATA_DIR      docker mode data directory (default: /var/lib/niuu)
#   NIUU_VERSION       release tag for mini mode (default: latest)
#   NIUU_INSTALL_DIR   where the `niuu` command goes (default: ~/.local/bin)
#   NIUU_NO_UP=1       install only, do not start the platform
#   NIUU_NO_PULL=1     docker mode: use the image already present, do not pull
#   NIUU_REPO          GitHub repo (default: niuulabs/niuu)
#   NIUU_REGISTRY      image registry (default: ghcr.io/niuulabs)
#
# Nothing is asked in the terminal. When the host is not ready (no Docker,
# not in the docker group, data directory not writable) the script stops and
# prints the exact commands to run; it never runs sudo for you.
set -eu

REPO="${NIUU_REPO:-niuulabs/niuu}"
REGISTRY="${NIUU_REGISTRY:-ghcr.io/niuulabs}"
INSTALL_DIR="${NIUU_INSTALL_DIR:-$HOME/.local/bin}"
VERSION="${NIUU_VERSION:-latest}"
IMAGE_TAG="${NIUU_IMAGE_TAG:-latest}"
DATA_DIR="${NIUU_DATA_DIR:-/var/lib/niuu}"
MODE="${NIUU_MODE:-docker}"
SOCKET="${DOCKER_HOST_SOCKET:-/var/run/docker.sock}"

say() { printf '%s\n' "$*" >&2; }
fail() { say "niuu: $*"; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "'$1' is required but not installed."; }

while [ $# -gt 0 ]; do
  case "$1" in
    --mode) [ $# -ge 2 ] || fail "--mode needs a value: docker or mini"; MODE="$2"; shift 2 ;;
    --mode=*) MODE="${1#--mode=}"; shift ;;
    -h|--help)
      sed -n '2,27p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//' >&2 || true
      exit 0
      ;;
    *) fail "unknown option: $1 (use --mode docker|mini)" ;;
  esac
done

case "$MODE" in
  docker|mini) ;;
  *) fail "unknown mode '$MODE'; use --mode docker or --mode mini" ;;
esac

# ---------------------------------------------------------------------------
# docker mode: the image is the CLI
# ---------------------------------------------------------------------------
install_docker_mode() {
  need docker
  need hostname

  if ! docker info >/dev/null 2>&1; then
    if [ "$(uname -s)" = "Linux" ] && ! id -nG 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
      say "niuu: Docker is installed but this user ($(id -un)) cannot talk to it."
      say "Add yourself to the docker group, then sign in again (or run 'newgrp docker') and rerun:"
      say "  sudo usermod -aG docker $(id -un)"
      exit 1
    fi
    fail "Docker is installed but the daemon is not reachable. Start it (e.g. 'sudo systemctl start docker') and rerun."
  fi
  docker compose version >/dev/null 2>&1 \
    || fail "Docker Compose v2 is missing. Install the compose plugin: https://docs.docker.com/compose/install/linux/"

  if ! mkdir -p "$DATA_DIR" 2>/dev/null || [ ! -w "$DATA_DIR" ]; then
    say "niuu: the data directory $DATA_DIR cannot be created or written by $(id -un)."
    say "Create it once, then rerun (or set NIUU_DATA_DIR to a writable path):"
    say "  sudo mkdir -p $DATA_DIR && sudo chown $(id -u):$(id -g) $DATA_DIR"
    exit 1
  fi
  mkdir -p "$HOME/.niuu" "$INSTALL_DIR"

  image="${REGISTRY}/niuu:${IMAGE_TAG}"
  skuld_image="${REGISTRY}/skuld:${IMAGE_TAG}"
  if [ "${NIUU_NO_PULL:-0}" = "1" ]; then
    docker image inspect "$image" >/dev/null 2>&1 || fail "NIUU_NO_PULL=1 but ${image} is not present locally"
  else
    say "Pulling ${image}…"
    docker pull -q "$image" >/dev/null || fail "could not pull ${image}"
  fi

  wrapper="${INSTALL_DIR}/niuu"
  cat > "$wrapper" <<EOF
#!/bin/sh
# niuu — runs the Niuu CLI from its platform image against this host's Docker.
# Written by the Niuu installer; rerun the installer to change the image tag.
set -eu
IMAGE="${image}"
SKULD_IMAGE="${skuld_image}"
DATA_DIR="${DATA_DIR}"
SOCKET="${SOCKET}"
tty=""
if [ -t 0 ] && [ -t 1 ]; then tty="-t"; fi
gpus=""
if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'; then gpus="--gpus all"; fi
# The socket keeps the host's group inside a container on Linux; Docker Desktop
# runs the daemon in a VM and presents it as root-owned, so root's group grants
# access there (the platform container is configured the same way).
if docker info --format '{{.OperatingSystem}}' 2>/dev/null | grep -q 'Docker Desktop'; then
  sock_gid=0
else
  sock_gid="\$(stat -c %g "\$SOCKET" 2>/dev/null || stat -f %g "\$SOCKET")"
fi
# Any NIUU_* variable in this shell reaches the CLI (NIUU_SERVER__PORT=8081 ...).
passthrough=""
for name in \$(env | sed -n 's/^\(NIUU_[A-Za-z0-9_]*\)=.*/\1/p'); do
  passthrough="\$passthrough -e \$name"
done
mkdir -p "\$HOME/.niuu"
# shellcheck disable=SC2086
exec docker run --rm -i \$tty \$gpus \$passthrough \\
  --network host --hostname "\$(hostname)" \\
  --user "\$(id -u):\$(id -g)" --group-add "\$sock_gid" \\
  -e HOME="\$HOME" -e NIUU_MODE=docker \\
  -e NIUU_DOCKER__IMAGE="\$IMAGE" -e NIUU_DOCKER__SKULD_IMAGE="\$SKULD_IMAGE" -e NIUU_DOCKER__DATA_DIR="\$DATA_DIR" \\
  -v "\$SOCKET:\$SOCKET" -v "\$HOME/.niuu:\$HOME/.niuu" -v "\$DATA_DIR:\$DATA_DIR" \\
  -v /etc/os-release:/etc/os-release:ro \\
  --entrypoint /opt/venv/bin/niuu "\$IMAGE" "\$@"
EOF
  chmod 0755 "$wrapper"
  say "Installed niuu to ${wrapper} (docker mode, image ${image})"
  case ":$PATH:" in
    *":${INSTALL_DIR}:"*) ;;
    *)
      say "Note: ${INSTALL_DIR} is not on your PATH. Add it with:"
      say "  export PATH=\"${INSTALL_DIR}:\$PATH\""
      ;;
  esac
  if [ "${NIUU_NO_UP:-0}" = "1" ]; then
    say "Run 'niuu up' to start the platform."
    exit 0
  fi
  exec "$wrapper" up
}

# ---------------------------------------------------------------------------
# mini mode: the release binary
# ---------------------------------------------------------------------------
install_mini_mode() {
  need curl
  need uname

  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  case "$os" in
    linux|darwin) ;;
    *) fail "unsupported OS: $os" ;;
  esac
  case "$arch" in
    x86_64|amd64) arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) fail "unsupported architecture: $arch" ;;
  esac
  asset="niuu-${os}-${arch}"

  if [ "$VERSION" = "latest" ]; then
    base="https://github.com/${REPO}/releases/latest/download"
  else
    base="https://github.com/${REPO}/releases/download/${VERSION}"
  fi

  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT

  say "Downloading ${asset} (${VERSION})…"
  curl -fsSL --retry 3 -o "${tmp}/${asset}" "${base}/${asset}" \
    || fail "download failed: ${base}/${asset}"
  curl -fsSL --retry 3 -o "${tmp}/checksums.txt" "${base}/checksums.txt" \
    || fail "download failed: ${base}/checksums.txt"

  expected="$(grep " ${asset}\$" "${tmp}/checksums.txt" | awk '{print $1}')"
  [ -n "$expected" ] || fail "no checksum for ${asset} in checksums.txt"
  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "${tmp}/${asset}" | awk '{print $1}')"
  else
    actual="$(shasum -a 256 "${tmp}/${asset}" | awk '{print $1}')"
  fi
  [ "$expected" = "$actual" ] || fail "checksum mismatch for ${asset}"

  mkdir -p "$INSTALL_DIR"
  install -m 0755 "${tmp}/${asset}" "${INSTALL_DIR}/niuu"
  say "Installed niuu to ${INSTALL_DIR}/niuu (mini mode)"

  case ":$PATH:" in
    *":${INSTALL_DIR}:"*) ;;
    *)
      say "Note: ${INSTALL_DIR} is not on your PATH. Add it with:"
      say "  export PATH=\"${INSTALL_DIR}:\$PATH\""
      ;;
  esac

  if [ "${NIUU_NO_UP:-0}" = "1" ]; then
    say "Run 'niuu platform init' then 'niuu platform up' to start."
    exit 0
  fi
  exec "${INSTALL_DIR}/niuu" platform up
}

case "$MODE" in
  docker) install_docker_mode ;;
  mini) install_mini_mode ;;
esac
