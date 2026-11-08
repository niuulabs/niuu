#!/bin/sh
# Built-in host recipe, stage tools, apply: install the Linux host runtime's packages with
# the distribution's package manager. Only apt (Debian, Ubuntu) and dnf (Fedora, RHEL)
# are known; any other distribution stops here and names what to install by hand.
set -eu
packages_apt="iproute2 iptables podman uidmap passt curl python3"
packages_dnf="iproute iptables-nft podman shadow-utils passt curl python3"
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $packages_apt
elif command -v dnf >/dev/null 2>&1; then
  # shellcheck disable=SC2086
  dnf install -y -q $packages_dnf
else
  echo "niuu host tools: no apt-get or dnf on this host; install $packages_apt (or your" \
    "distribution's names for them) and add the host again" >&2
  exit 1
fi
