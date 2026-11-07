#!/bin/sh
# Built-in host recipe, stage nix, check: 0 when install-nix.sh's pinned Nix, its daemon,
# the nixpkgs registry and its GC root are in place (Linux or macOS), 1 to run the
# installer. NIX_VERSION must equal install-nix.sh's default (a test keeps them equal).
set -u
NIX_VERSION=2.35.4
nix=/nix/var/nix/profiles/default/bin/nix
[ -x "$nix" ] || exit 1
"$nix" --version | grep -qx "nix (Nix) $NIX_VERSION" || exit 1
case "$(uname -s)" in
  Darwin) launchctl print system/org.nixos.nix-daemon >/dev/null 2>&1 || exit 1 ;;
  Linux) systemctl is-active --quiet nix-daemon.socket nix-daemon.service || exit 1 ;;
  *) exit 2 ;;
esac
grep -q '"type": "path"' /etc/nix/registry.json 2>/dev/null || exit 1
[ -e /nix/var/nix/gcroots/niuu-nixpkgs ] || exit 1
exit 0
