#!/usr/bin/env bash
# Install the pinned, checksum-verified Syft and Grype that scan.sh runs.
# Grype's version fixes its DB schema; Syft matches the Syft library Grype embeds,
# so the SBOM scan.sh feeds Grype catalogs exactly what Grype would itself.
set -euo pipefail

GRYPE_VERSION=0.118.0
GRYPE_SHA256=1d444c5e7360471815f7158f71935fcecc68a3c417d85c7344f770854300bba2
SYFT_VERSION=1.51.1 # github.com/anchore/syft in grype v0.118.0's go.mod
SYFT_SHA256=8fcb33017a0dc1058298c923c436d19dfa68ae93968e0b423248542e3afb9fc3

bin_dir=${1:-/usr/local/bin}

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) platform=linux_amd64 ;;
  Darwin-arm64)
    platform=darwin_arm64
    GRYPE_SHA256=938f050bb5076c8aa761867b39843abad2414dfe4cc82b7d36886e634f49c640
    SYFT_SHA256=ac063af3b9874769deb7ea1e6d76841e68f9e3bb50cd654226fc977de65532c1
    ;;
  *) echo "install-scanners.sh: only linux amd64 and darwin arm64 are pinned" >&2; exit 1 ;;
esac

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

install_tool() {
  local name=$1 version=$2 sha256=$3
  local archive="${name}_${version}_${platform}.tar.gz"
  curl --retry 5 --retry-all-errors --retry-delay 2 -sSfL \
    "https://github.com/anchore/${name}/releases/download/v${version}/${archive}" \
    -o "$work_dir/$archive"
  echo "$sha256  $work_dir/$archive" | shasum -a 256 -c -
  tar -xzf "$work_dir/$archive" -C "$work_dir" "$name"
  install -m 0755 "$work_dir/$name" "$bin_dir/$name"
}

install_tool grype "$GRYPE_VERSION" "$GRYPE_SHA256"
install_tool syft "$SYFT_VERSION" "$SYFT_SHA256"
"$bin_dir/grype" version
"$bin_dir/syft" version

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "grype-version=$GRYPE_VERSION" >> "$GITHUB_OUTPUT"
  echo "syft-version=$SYFT_VERSION" >> "$GITHUB_OUTPUT"
fi
