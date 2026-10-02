#!/bin/sh
# npm ships its dependencies bundled inside its own tarball, so neither a newer
# lockfile nor `overrides` can patch them. Every direct dependency here other
# than npm is a fixed release of a package npm bundles; move it over the
# bundled copy. Fails the build if npm no longer bundles the package (drop the
# pin) or the swap did not take.
set -eu
cd "$(dirname "$0")"
bundled=node_modules/npm/node_modules
for name in $(node -p "Object.keys(require('./package.json').dependencies).filter((n) => n !== 'npm').join(' ')"); do
  if [ ! -d "$bundled/$name" ]; then
    echo "npm no longer bundles $name; remove it from npm-bootstrap/package.json" >&2
    exit 1
  fi
  want=$(node -p "require('./node_modules/$name/package.json').version")
  rm -rf "${bundled:?}/$name"
  mv "node_modules/$name" "$bundled/$name"
  got=$(node -p "require('./$bundled/$name/package.json').version")
  if [ "$got" != "$want" ]; then
    echo "bundled $name is $got after replacement, expected $want" >&2
    exit 1
  fi
  echo "replaced npm's bundled $name with $want"
done
