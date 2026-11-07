#!/bin/sh
# Built-in host recipe, stage tools, verify: every command the Linux host runtime runs is
# there (a Mac: nothing to verify). Prints no facts.
set -eu
[ "$(uname -s)" = Linux ] || exit 0
for command in ip iptables iptables-save systemd-run curl python3 podman newuidmap \
    newgidmap pasta; do
  command -v "$command" >/dev/null
done
python3 -c 'import tarfile; tarfile.tar_filter'
