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
#   NIUU_DATA_DIR      docker mode data directory (default: ~/.niuu/data, yours; no root)
#   NIUU_VERSION       release tag for mini mode (default: latest)
#   NIUU_INSTALL_DIR   where the `niuu` command goes (default: ~/.local/bin)
#   NIUU_NO_UP=1       install only, do not start the platform
#   NIUU_NO_PULL=1     docker mode: use the image already present, do not pull
#   NIUU_SKIP_GPU=1    docker mode: start even though the GPU cannot reach Docker
#   NIUU_REPO          GitHub repo (default: niuulabs/niuu)
#   NIUU_REGISTRY      image registry (default: ghcr.io/niuulabs)
#
# Nothing is asked in the terminal and nothing needs root: the script never
# runs sudo. When the host is not ready it stops, explains what it found and
# prints the exact commands for you to run, then you rerun it. That covers a
# missing Docker, a user outside the docker group, a chosen data directory you
# cannot write, and an NVIDIA GPU that Docker cannot use yet (no NVIDIA runtime
# registered), since without that no session or local model could use the GPU.
set -eu

REPO="${NIUU_REPO:-niuulabs/niuu}"
REGISTRY="${NIUU_REGISTRY:-ghcr.io/niuulabs}"
INSTALL_DIR="${NIUU_INSTALL_DIR:-$HOME/.local/bin}"
VERSION="${NIUU_VERSION:-latest}"
IMAGE_TAG="${NIUU_IMAGE_TAG:-latest}"
DATA_DIR="${NIUU_DATA_DIR:-$HOME/.niuu/data}"
MODE="${NIUU_MODE:-docker}"
SOCKET="${DOCKER_HOST_SOCKET:-}"

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
# GPU: a host with an NVIDIA GPU must have the NVIDIA runtime registered with Docker
# ---------------------------------------------------------------------------
NVIDIA_TOOLKIT_GUIDE="https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"

host_has_nvidia_gpu() {
  command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1
}

docker_has_nvidia_runtime() {
  docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'
}

check_nvidia_runtime() {
  [ "$(uname -s)" = "Darwin" ] && return 0
  [ "${NIUU_SKIP_GPU:-0}" = "1" ] && return 0
  host_has_nvidia_gpu || return 0
  if docker_has_nvidia_runtime; then
    say "NVIDIA GPU found and Docker has the NVIDIA runtime."
    return 0
  fi
  gpu_name="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
  say "niuu: GPU found (${gpu_name:-NVIDIA}), but Docker has no NVIDIA runtime registered."
  say "Containers cannot use the GPU until it is. Not started."
  if command -v nvidia-ctk >/dev/null 2>&1; then
    say "The NVIDIA Container Toolkit is installed. Register it, then rerun this installer:"
    say "  sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
  else
    say "Install the NVIDIA Container Toolkit (${NVIDIA_TOOLKIT_GUIDE}), register it, then rerun this installer:"
    say "  sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
  fi
  say "To start without the GPU: NIUU_SKIP_GPU=1"
  exit 1
}

# ---------------------------------------------------------------------------
# docker mode: the image is the CLI
# ---------------------------------------------------------------------------
# Initial configuration: written once into ~/.niuu/config.yaml, which the CLI
# container mounts and `niuu up` reads. The vLLM image and the models the
# wizard offers live here, not in the platform image, so changing them is an
# edit to this file and `niuu up`, never a new build. An existing file is
# yours and is left alone.
# ---------------------------------------------------------------------------
write_initial_config() {
  config="$HOME/.niuu/config.yaml"
  if [ -f "$config" ]; then
    say "Keeping your existing ${config}."
    return 0
  fi
  cat > "$config" <<'EOF'
# Niuu configuration, written by the installer. Edit and run `niuu up` to apply.
mode: docker
docker:
  vllm:
    # NVIDIA's vLLM container (arm64 and amd64); its release notes list DGX Spark.
    image: nvcr.io/nvidia/vllm:26.08-py3
  # Models the setup wizard offers to serve locally. Sizes are what vLLM
  # reserves for the weights plus a 64k-token KV cache. `serve_args` come
  # from each model card: the tool-call parser (agents get tool calls back),
  # the reasoning parser (the model's thinking is separated from its answer
  # instead of arriving inside it), a sequence cap on unified memory.
  models:
    - id: nemotron-3-nano-30b
      model: nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16
      name: NVIDIA Nemotron 3 Nano 30B
      description: Fast agentic coder tuned by NVIDIA. Best default for sessions and residents.
      weight_gib: 62
      recommended: true
      trust_remote_code: true
      serve_args: ["--max-num-seqs", "8", "--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder", "--reasoning-parser", "nemotron_v3"]
    - id: gpt-oss-120b
      model: openai/gpt-oss-120b
      name: OpenAI gpt-oss-120b
      description: Larger reasoning model. Slower per token, stronger on planning.
      weight_gib: 78
    - id: qwen3-coder-30b
      model: Qwen/Qwen3-Coder-30B-A3B-Instruct
      name: Qwen3-Coder 30B-A3B
      description: Lean coding model with generous headroom for long contexts.
      weight_gib: 24
      serve_args: ["--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder"]
EOF
  say "Wrote ${config}."
}

install_docker_mode() {
  command -v docker >/dev/null 2>&1 \
    || fail "Docker Engine and Compose are required. On macOS, start Docker Desktop or Colima with '--runtime docker'. A containerd-only runtime does not expose the Docker API Niuu uses."
  need hostname

  if ! docker info >/dev/null 2>&1; then
    if [ "$(uname -s)" = "Linux" ] && ! id -nG 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
      say "niuu: Docker is installed but this user ($(id -un)) is not allowed to use it."
      say "Fix it in three steps:"
      say "  1. sudo usermod -aG docker $(id -un)"
      say "  2. sign out and back in (or run: newgrp docker) so the group applies"
      say "  3. run this installer again"
      exit 1
    fi
    if [ "$(uname -s)" = "Darwin" ]; then
      fail "Docker is not reachable. Start Docker Desktop or run 'colima start --runtime docker', then check 'docker context ls'."
    fi
    fail "Docker is installed but the daemon is not reachable. Start it (e.g. 'sudo systemctl start docker') and rerun."
  fi
  docker compose version >/dev/null 2>&1 \
    || fail "Docker Compose v2 is missing. Install the Compose plugin for your Docker runtime, then confirm 'docker compose version' works."
  check_nvidia_runtime

  endpoint="${DOCKER_HOST:-}"
  if [ -n "${DOCKER_CONTEXT:-}" ] || [ -z "$endpoint" ]; then
    endpoint="$(docker context inspect --format '{{.Endpoints.docker.Host}}')"
  fi
  case "$endpoint" in
    unix://*) ;;
    *) fail "Docker mode needs a local Unix socket for host file mounts; selected endpoint is '$endpoint'. Select a local Docker context." ;;
  esac
  if [ -z "$SOCKET" ]; then
    SOCKET="${endpoint#unix://}"
    # Docker resolves bind sources inside its Linux VM, not in the macOS client.
    [ "$(uname -s)" != "Darwin" ] || SOCKET=/var/run/docker.sock
  fi

  daemon_arch="$(docker info --format '{{.Architecture}}')"
  case "$daemon_arch" in
    x86_64|amd64) daemon_arch=amd64 ;;
    aarch64|arm64) daemon_arch=arm64 ;;
    *) fail "unsupported Docker daemon architecture: $daemon_arch" ;;
  esac
  native_arch="$(uname -m)"
  case "$native_arch" in
    x86_64|amd64) native_arch=amd64 ;;
    aarch64|arm64) native_arch=arm64 ;;
  esac
  if [ "$native_arch" != "$daemon_arch" ]; then
    say "Architecture mismatch: this host is $native_arch but Docker runs $daemon_arch. Using linux/$daemon_arch images. Check the VM architecture in your Docker/Colima configuration to run natively."
  fi

  if ! mkdir -p "$DATA_DIR" 2>/dev/null || [ ! -w "$DATA_DIR" ]; then
    say "niuu: the data directory $DATA_DIR cannot be created or written by $(id -un)."
    say "Either point NIUU_DATA_DIR at a directory you own and rerun, or create this one once:"
    say "  sudo mkdir -p $DATA_DIR && sudo chown $(id -u):$(id -g) $DATA_DIR"
    exit 1
  fi
  mkdir -p "$HOME/.niuu" "$INSTALL_DIR"
  write_initial_config

  image="${REGISTRY}/niuu:${IMAGE_TAG}"
  skuld_image="${REGISTRY}/skuld:${IMAGE_TAG}"
  if [ "${NIUU_NO_PULL:-0}" = "1" ]; then
    docker image inspect "$image" >/dev/null 2>&1 || fail "NIUU_NO_PULL=1 but ${image} is not present locally"
  else
    say "Pulling ${image}…"
    docker pull -q --platform "linux/$daemon_arch" "$image" >/dev/null || fail "could not pull ${image}"
  fi
  image_arch="$(docker image inspect --format '{{.Architecture}}' "$image")"
  [ "$image_arch" = "$daemon_arch" ] \
    || fail "${image} is $image_arch but Docker runs $daemon_arch. Pull the image with '--platform linux/$daemon_arch' and rerun."

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
PLATFORM="linux/${daemon_arch}"
tty=""
if [ -t 0 ] && [ -t 1 ]; then tty="-t"; fi
gpus=""
if [ "\$(uname -s)" != Darwin ] && docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'; then gpus="--gpus all"; fi
# The socket keeps the host's group inside a container on Linux; Docker Desktop
# runs the daemon in a VM and presents it as root-owned, so root's group grants
# access there (the platform container is configured the same way).
if [ "\$(uname -s)" = Darwin ]; then
  sock_gid="\$(docker run --rm --platform "\$PLATFORM" --user 0:0 -v "\$SOCKET:/var/run/docker.sock" --entrypoint stat "\$IMAGE" -c %g /var/run/docker.sock)"
elif docker info --format '{{.OperatingSystem}}' 2>/dev/null | grep -q 'Docker Desktop'; then
  sock_gid=0
else
  sock_gid="\$(stat -c %g "\$SOCKET" 2>/dev/null || stat -f %g "\$SOCKET")"
fi
# Detect the advertised address on macOS before entering the Docker VM.
# Keep the existing server.external_host setting authoritative inside the CLI.
export NIUU_DOCKER__HOST_OS="\$(uname -s)"
export NIUU_DOCKER__HOST_ARCH="\$(uname -m)"
if [ "\$NIUU_DOCKER__HOST_OS" = Darwin ] && [ -z "\${NIUU_DOCKER__HOST_LAN_IP:-}" ]; then
  interface="\$(route -n get default 2>/dev/null | awk '/interface:/ {print \$2}')"
  NIUU_DOCKER__HOST_LAN_IP="\$(ipconfig getifaddr "\$interface" 2>/dev/null || true)"
  [ -n "\$NIUU_DOCKER__HOST_LAN_IP" ] || { printf '%s\\n' 'Cannot determine the macOS LAN address; set NIUU_DOCKER__HOST_LAN_IP.' >&2; exit 1; }
  export NIUU_DOCKER__HOST_LAN_IP
fi
# Any NIUU_* variable in this shell reaches the CLI (NIUU_SERVER__PORT=8081 ...).
passthrough=""
for name in \$(env | sed -n 's/^\(NIUU_[A-Za-z0-9_]*\)=.*/\1/p'); do
  passthrough="\$passthrough -e \$name"
done
mkdir -p "\$HOME/.niuu"
set -- --entrypoint /opt/venv/bin/niuu "\$IMAGE" "\$@"
if [ "\$NIUU_DOCKER__HOST_OS" = Linux ] && [ -f /etc/os-release ]; then set -- -v /etc/os-release:/etc/os-release:ro "\$@"; fi
# shellcheck disable=SC2086
exec docker run --rm -i --platform "\$PLATFORM" \$tty \$gpus \$passthrough \\
  --network host --hostname "\$(hostname)" \\
  --user "\$(id -u):\$(id -g)" --group-add "\$sock_gid" \\
  -e HOME="\$HOME" -e NIUU_MODE=docker \\
  -e NIUU_DOCKER__IMAGE="\$IMAGE" -e NIUU_DOCKER__SKULD_IMAGE="\$SKULD_IMAGE" -e NIUU_DOCKER__DATA_DIR="\$DATA_DIR" \\
  -e NIUU_DOCKER__SOCKET_PATH="\$SOCKET" \\
  -v "\$SOCKET:/var/run/docker.sock" -v "\$HOME/.niuu:\$HOME/.niuu" -v "\$DATA_DIR:\$DATA_DIR" \\
  "\$@"
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
