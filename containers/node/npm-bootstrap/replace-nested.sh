#!/bin/sh
# Usage: replace-nested.sh <install-dir> <nested-node_modules> <package>...
#
# Some packages pin their dependencies where a lockfile and `overrides`
# cannot reach them: npm bundles its own, and pi-coding-agent ships an
# npm-shrinkwrap.json. Each <package> must be locked as a direct dependency
# of <install-dir>; this copies that locked release over the pinned copy
# under <nested-node_modules>. Fails the build if the pinned copy is gone
# (drop the replacement) or the copy did not take.
set -eu
install_dir=$1
nested=$2
shift 2
cd "$install_dir"
for name in "$@"; do
  if [ ! -d "$nested/$name" ]; then
    echo "$nested no longer contains $name; remove its replacement" >&2
    exit 1
  fi
  want=$(node -p "require('./node_modules/$name/package.json').version")
  rm -rf "${nested:?}/$name"
  cp -R "node_modules/$name" "$nested/$name"
  got=$(node -p "require('./$nested/$name/package.json').version")
  if [ "$got" != "$want" ]; then
    echo "$nested/$name is $got after replacement, expected $want" >&2
    exit 1
  fi
  echo "replaced $nested/$name with $want"
done
