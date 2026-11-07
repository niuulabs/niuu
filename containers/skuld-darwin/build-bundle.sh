#!/bin/sh
# Build the macOS session bundle: what the Skuld image gives a Linux session, for a Mac.
#
# Runs on macOS (arm64), from the repository root:
#   containers/skuld-darwin/build-bundle.sh OUTPUT.tar.gz
#
# The bundle is one directory tree, relocatable, read-only once unpacked:
#   python/      a uv-managed (python-build-standalone) CPython, which runs from any path
#   site/        the niuu wheel and its locked dependencies (uv.lock, hashes required)
#   node/        Node.js, for the npm CLIs
#   tools/       the agent CLIs from containers/skuld/npm-tools (package-lock integrity)
#   bin/         the agent CLIs; fallback/ uv and node; libexec/ Skuld, its Python, bao
#   bundle.json  its format and platform, which the darwin install checks
#
# Every download is pinned by version and SHA-256; npm and uv verify the lockfiles' hashes.
# Nothing auto-updates. The versions follow the Linux Skuld and OpenShell images; bump
# them together.
set -eu

OUTPUT=${1:?usage: build-bundle.sh OUTPUT.tar.gz}
[ "$(uname -s)" = Darwin ] || { echo "build the macOS bundle on macOS" >&2; exit 1; }
[ "$(uname -m)" = arm64 ] || { echo "the macOS bundle is built for arm64" >&2; exit 1; }

UV_VERSION=0.13.7
UV_SHA256=81b811b57e4624df7ed3aac148e0359100d9962f3d9dce00aec533dc2e086731
PYTHON_VERSION=3.14.10
NODE_VERSION=24.22.2
NODE_SHA256=aa9549349e47dffaa42dcf95d6e06ade8c4bdafc980979883b88767670df385a
# The OpenBao release of the guest agent image the Forges pin (OpenBao 2.5.3).
BAO_VERSION=2.5.3
BAO_SHA256=1bb5fdc60226bfc142f70590342fee642ccdea602986c4ff7bd92fed84f259b9
# Muse as in containers/skuld/install-muse.sh, from the same release manifest.
MUSE_VERSION=1.3.0-R3233.1
MUSE_SHA256=4395928df3527ced7613f18d74aa7bb1c383290c01ae5bbf792d996fa949452e

ROOT=$(pwd)
[ -f "$ROOT/uv.lock" ] && [ -d "$ROOT/containers/skuld/npm-tools" ] || {
  echo "run from the repository root" >&2; exit 1; }
WORK=$(mktemp -d "${TMPDIR:-/tmp}/skuld-darwin.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
B="$WORK/bundle"
mkdir -p "$B/bin" "$WORK/downloads"

fetch() {  # url sha256 destination
  curl --fail --show-error --silent --location --retry 3 "$1" --output "$3"
  printf '%s  %s\n' "$2" "$3" | shasum -a 256 -c - >/dev/null || {
    echo "checksum mismatch for $1" >&2; exit 1; }
}

# uv, which builds the bundle and ships in it (the Linux image's fallback tool).
fetch "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-aarch64-apple-darwin.tar.gz" \
  "$UV_SHA256" "$WORK/downloads/uv.tar.gz"
tar -xzf "$WORK/downloads/uv.tar.gz" -C "$WORK/downloads"
install -m 0755 "$WORK/downloads/uv-aarch64-apple-darwin/uv" "$WORK/downloads/uv-aarch64-apple-darwin/uvx" "$B/bin/"
UV="$B/bin/uv"
export UV_NO_CONFIG=1 UV_PYTHON_DOWNLOADS=manual UV_CACHE_DIR="$WORK/uv-cache"

# Python: uv checks the download against the hashes this uv release pins.
UV_PYTHON_INSTALL_DIR="$WORK/pythons" "$UV" python install --no-bin "$PYTHON_VERSION"
installed=$(UV_PYTHON_INSTALL_DIR="$WORK/pythons" "$UV" python find --managed-python "$PYTHON_VERSION")
# The stdlib security backports every Linux image's Python carries (containers/
# python-security): built from checksum-pinned upstream inputs, installed only over the
# exact upstream modules, then verified with their exploit regressions. Where uv installed
# it, before the move: the verify reads the shared library at the install's own LIBDIR.
"$installed" "$ROOT/containers/python-security/build.py" "$WORK/patched-stdlib"
"$installed" "$ROOT/containers/python-security/install.py" "$WORK/patched-stdlib"
"$installed" "$ROOT/containers/python-security/verify.py" skuld-darwin > "$WORK/python-security.json"
mv "$(dirname "$(dirname "$installed")")" "$B/python"
PYTHON="$B/python/bin/python3"
"$PYTHON" -c 'import ssl, sqlite3, sys; assert sys.version_info >= (3, 12)'

# niuu and its locked dependencies, as the Skuld image installs them (plus otel).
"$UV" build --wheel --out-dir "$WORK/dist" "$ROOT" >/dev/null
"$UV" export --project "$ROOT" --frozen --no-dev --no-emit-project --extra otel \
  --format requirements-txt --output-file "$WORK/requirements.txt" >/dev/null
"$UV" pip install --python "$PYTHON" --target "$B/site" --require-hashes --no-deps \
  --no-build --requirement "$WORK/requirements.txt"
"$UV" pip install --python "$PYTHON" --target "$B/site" --no-deps --no-build "$WORK"/dist/*.whl
PYTHONPATH="$B/site" "$PYTHON" -c 'import skuld.broker, volundr.guest_agent'

# Node and the agent CLIs from the image's lockfile; npm verifies every package's integrity
# and installs the darwin-arm64 builds of the native CLIs.
fetch "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-darwin-arm64.tar.gz" \
  "$NODE_SHA256" "$WORK/downloads/node.tar.gz"
tar -xzf "$WORK/downloads/node.tar.gz" -C "$WORK/downloads"
mv "$WORK/downloads/node-v$NODE_VERSION-darwin-arm64" "$B/node"
mkdir -p "$B/tools"
cp "$ROOT/containers/skuld/npm-tools/package.json" "$ROOT/containers/skuld/npm-tools/package-lock.json" "$B/tools/"
(
  cd "$B/tools"
  PATH="$B/node/bin:$PATH" GROK_HOME="$WORK/grok-install" \
    npm ci --omit=dev --cache "$WORK/npm-cache" --no-audit --no-fund >/dev/null
)

fetch "https://github.com/openbao/openbao/releases/download/v$BAO_VERSION/bao_${BAO_VERSION}_Darwin_arm64.tar.gz" \
  "$BAO_SHA256" "$WORK/downloads/bao.tar.gz"
tar -xzf "$WORK/downloads/bao.tar.gz" -C "$WORK/downloads" bao
install -m 0755 "$WORK/downloads/bao" "$B/bin/bao"
fetch "https://lookaside.facebook.com/lookaside/muse/download/?channel=muse&version=$MUSE_VERSION&file=muse-aarch64-macos" \
  "$MUSE_SHA256" "$B/bin/muse"
chmod 0755 "$B/bin/muse"

# Wrappers: each finds the bundle from its own path, so the bundle runs wherever it is
# unpacked, and never picks an interpreter from the Mac's PATH. bin/ holds the agent CLIs
# (first on a session's PATH), fallback/ the tools Nix or the Mac may provide instead (last
# on it), and libexec/ what only Forge's own programs run: the Python Skuld runs on, OpenBao.
mkdir -p "$B/fallback" "$B/libexec"
wrapper() {  # directory, name, command line
  {
    printf '#!/bin/sh\n'
    printf 'here=$(cd "$(dirname "$0")/.." && pwd -P)\n'
    printf '%s\n' "$3"
  } > "$B/$1/$2"
  chmod 0755 "$B/$1/$2"
}
mv "$B/bin/uv" "$B/bin/uvx" "$B/fallback/"
mv "$B/bin/bao" "$B/libexec/"
for name in python python3; do
  wrapper libexec "$name" 'PYTHONPATH="$here/site${PYTHONPATH:+:$PYTHONPATH}" exec "$here/python/bin/python3" "$@"'
done
# The agent CLIs a host session runs (host_session_programs, the list both host runtimes
# put first on a session's PATH); muse is a download of its own, above.
AGENTS=$("$B/libexec/python" -c 'from volundr.adapters.outbound.host_session_programs import AGENT_COMMANDS; print(" ".join(a for a in AGENT_COMMANDS if a != "muse"))')
for agent in $AGENTS; do
  [ -e "$B/tools/node_modules/.bin/$agent" ] || { echo "the npm tools give no $agent" >&2; exit 1; }
  wrapper bin "$agent" "PATH=\"\$here/node/bin:\$PATH\" exec \"\$here/tools/node_modules/.bin/$agent\" \"\$@\""
done
for tool in node npm npx; do
  wrapper fallback "$tool" "exec \"\$here/node/bin/$tool\" \"\$@\""
done

# Every advertised command runs here, as the Linux image's build checks.
for command in $(for a in $AGENTS muse; do echo "bin/$a"; done) libexec/bao \
    fallback/uv fallback/node; do
  "$B/$command" --version >/dev/null || { echo "$command does not run" >&2; exit 1; }
done
"$B/libexec/python" -c 'import skuld.broker, volundr.guest_agent'

# What the darwin runtime's install checks before it unpacks the bundle on a Mac.
SOURCE=$(git -C "$ROOT" rev-parse HEAD)
"$PYTHON" -c 'import json, sys; json.dump({"format": 1, "platform": "darwin-arm64", "source": sys.argv[2]}, open(sys.argv[1], "w"), indent=2)' "$B/bundle.json" "$SOURCE"

# A stable archive: sorted, no owners or times of this machine.
find "$B" -exec touch -h -t 202601010000 {} +
tar --no-xattrs --uid 0 --gid 0 -czf "$OUTPUT" -C "$B" .
shasum -a 256 "$OUTPUT"
