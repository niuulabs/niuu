#!/bin/sh
# Niuu installer — the one command for a clean machine.
#
#   curl -fsSL https://get.niuu.ai | sh
#
# Downloads the `niuu` CLI release binary for this OS/arch, verifies its
# checksum, installs it to ~/.local/bin (or $NIUU_INSTALL_DIR), and then runs
# `niuu up` in docker mode unless NIUU_NO_UP=1. Nothing is asked in the
# terminal; the browser wizard takes it from there.
#
# Environment:
#   NIUU_VERSION       release tag to install (default: latest)
#   NIUU_INSTALL_DIR   target directory (default: ~/.local/bin)
#   NIUU_NO_UP=1       install only, do not start the platform
#   NIUU_REPO          GitHub repo (default: niuulabs/niuu)
set -eu

REPO="${NIUU_REPO:-niuulabs/niuu}"
INSTALL_DIR="${NIUU_INSTALL_DIR:-$HOME/.local/bin}"
VERSION="${NIUU_VERSION:-latest}"

say() { printf '%s\n' "$*" >&2; }
fail() { say "niuu: $*"; exit 1; }

need() {
  command -v "$1" >/dev/null 2>&1 || fail "'$1' is required but not installed."
}

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
say "Installed niuu to ${INSTALL_DIR}/niuu"

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

exec "${INSTALL_DIR}/niuu" up --mode docker
