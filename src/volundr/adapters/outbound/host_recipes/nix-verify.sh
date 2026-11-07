#!/bin/sh
# Built-in host recipe, stage nix, verify: the daemon answers and the Nix release the
# host execution requires (fact "nix").
set -eu
nix=/nix/var/nix/profiles/default/bin/nix
version="$("$nix" --version | sed 's/^nix (Nix) //')"
test -S /nix/var/nix/daemon-socket/socket
test -e /nix/var/nix/gcroots/niuu-nixpkgs
printf '{"nix": "%s"}\n' "$version"
