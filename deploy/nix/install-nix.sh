#!/bin/sh
# Install a pinned, multi-user Nix on a Linux host or a Mac: the shared software layer of
# Forge host-mode sessions (docs/operator/vm-compute.md "Host mode", and "Macs as hosts" in
# docs/operator/compute-hosts.md). Run as root. On a Mac the installer adds its own APFS
# volume, its build users and the org.nixos.nix-daemon launchd job.
# Idempotent: a host that already runs this Nix release is left as it is.
#
# Environment (all optional):
#   NIX_VERSION, NIX_SHA256_X86_64, NIX_SHA256_AARCH64, NIX_SHA256_AARCH64_DARWIN
#                    the release and its tarball digests
#   NIX_EXTRA_CONF   extra lines for /etc/nix/nix.conf (auto-GC thresholds, caches)
#   NIX_NIXPKGS      what `nixpkgs` means in `nix profile install nixpkgs#<package>`: a
#                    locked flake reference (a revision and its narHash). Locked, no user
#                    resolves a branch through the GitHub API, which rate-limits a shared
#                    egress address, and every user gets the same packages from one source
#                    in the store.
#   NIX_BASELINE     a flake output installed into the default profile every user
#                    has on PATH, e.g. github:niuulabs/niuu/<rev>?dir=deploy/nix#baseline
set -eu

NIX_VERSION="${NIX_VERSION:-2.35.4}"
NIX_SHA256_X86_64="${NIX_SHA256_X86_64:-ff66236d2f7e2a21f3350e73668e021ac6a8881ef277a38e16e952bfb22718c3}"
NIX_SHA256_AARCH64="${NIX_SHA256_AARCH64:-14a940a2d0ae5b2c623dc2ca168311c9367fd4bc416f6ded0c1087089d6de934}"
NIX_SHA256_AARCH64_DARWIN="${NIX_SHA256_AARCH64_DARWIN:-ed0c6b25cc15e29c6cbc78d4c20a8e7add3f5f60fdf7b95dd5e99d74e9ba4e45}"
NIX_NIXPKGS="${NIX_NIXPKGS:-github:NixOS/nixpkgs/c56a87d8e41ce69bd2af57eb35dcc117a9dfca33?narHash=sha256-/cpdPHaySDIyCgZI6EWngiM6jK3YLUNzv1b7lTzy6Bk%3D}"
NIX=/nix/var/nix/profiles/default/bin/nix

case "$(uname -s)/$(uname -m)" in
  Linux/x86_64) system=x86_64-linux; digest="$NIX_SHA256_X86_64" ;;
  Linux/aarch64) system=aarch64-linux; digest="$NIX_SHA256_AARCH64" ;;
  Darwin/arm64) system=aarch64-darwin; digest="$NIX_SHA256_AARCH64_DARWIN" ;;
  *) echo "install-nix: unsupported system $(uname -s) $(uname -m)" >&2; exit 1 ;;
esac
if command -v sha256sum >/dev/null 2>&1; then
  check_sha256() { sha256sum --check --quiet -; }
else
  check_sha256() { shasum -a 256 --check --quiet -; }
fi

if [ -x "$NIX" ] && "$NIX" --version | grep -qx "nix (Nix) $NIX_VERSION"; then
  echo "install-nix: Nix $NIX_VERSION is installed"
else
  if [ -e /nix ]; then
    echo "install-nix: /nix exists without Nix $NIX_VERSION; remove it or rebuild the host" >&2
    exit 1
  fi
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  name="nix-$NIX_VERSION-$system"
  curl --fail --silent --show-error --location --retry 3 \
    "https://releases.nixos.org/nix/nix-$NIX_VERSION/$name.tar.xz" --output "$work/nix.tar.xz"
  printf '%s  %s\n' "$digest" "$work/nix.tar.xz" | check_sha256
  tar -xJf "$work/nix.tar.xz" -C "$work"
  # Untrusted users build through the daemon into one store: they may add packages,
  # never substituters or unsigned paths, and never change each other's profiles.
  cat > "$work/nix.conf" <<CONF
experimental-features = nix-command flakes
allowed-users = *
trusted-users = root
${NIX_EXTRA_CONF:-}
CONF
  "$work/$name/install" --daemon --yes --no-channel-add --nix-extra-conf-file "$work/nix.conf"
fi

# One copy of nixpkgs' source in the store, rooted so garbage collection keeps it, is
# what `nixpkgs` means for every user: none downloads or unpacks it again.
source="$("$NIX" flake prefetch --json "$NIX_NIXPKGS" | python3 -c 'import json, sys; print(json.load(sys.stdin)["storePath"])')"
"$NIX" build --out-link /nix/var/nix/gcroots/niuu-nixpkgs "$source"
"$NIX" registry add --registry /etc/nix/registry.json nixpkgs "path:$source"
chmod 0644 /etc/nix/registry.json
if [ -n "${NIX_BASELINE:-}" ]; then
  "$NIX" profile install --profile /nix/var/nix/profiles/default "$NIX_BASELINE"
fi
"$NIX" --version
