#!/bin/sh
# Built-in host recipe, stage tools, check: what the Linux host runtime needs besides Nix
# (slots: iproute2, iptables, systemd-run; rootless containers: podman, uidmap, passt).
# A Mac needs nothing here: 0. Linux: 0 when everything is there, 1 to install it.
set -u
[ "$(uname -s)" = Linux ] || exit 0
for command in ip iptables iptables-save systemd-run curl python3 podman newuidmap \
    newgidmap pasta; do
  command -v "$command" >/dev/null 2>&1 || exit 1
done
python3 -c 'import tarfile; tarfile.tar_filter' >/dev/null 2>&1 || exit 1
exit 0
