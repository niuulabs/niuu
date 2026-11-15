#!/usr/bin/env bash
# Compile gbrain into one executable for mini mode's memory (build/gbrain/gbrain).
#
# The commit and the security patch are the ones containers/gbrain/Dockerfile builds,
# so a laptop and a cluster run the same gbrain. `bun build --compile` (gbrain's own
# `build` script) embeds Bun, gbrain and PGlite, so nothing else is needed at run time;
# `niuu platform up` runs it and the release binary carries it (src/cli/build.py).
#
# Needs git and Bun >= 1.3.11 (the Dockerfile's image runs 1.4.4).
#
# Optional environment variables:
#   OUTPUT_DIR  — where the executable goes (default build/gbrain)
#   SOURCE_DIR  — the gbrain checkout to build in (default build/gbrain-src)
#
# Idempotent: skips the build when OUTPUT_DIR/REF already names the pinned commit.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKERFILE="$REPO_ROOT/containers/gbrain/Dockerfile"
PATCH="$REPO_ROOT/containers/gbrain/security-deps.patch"
OUTPUT_DIR="${OUTPUT_DIR:-$REPO_ROOT/build/gbrain}"
SOURCE_DIR="${SOURCE_DIR:-$REPO_ROOT/build/gbrain-src}"
MIN_BUN="1.3.11"

GBRAIN_REF="$(sed -n 's/^ARG GBRAIN_REF=//p' "$DOCKERFILE")"
if [ -z "$GBRAIN_REF" ]; then
    echo "No 'ARG GBRAIN_REF=' line in $DOCKERFILE" >&2
    exit 1
fi
# The patch is part of what is built, so a changed patch rebuilds.
PATCH_SUM="$(shasum -a 256 "$PATCH" | cut -d' ' -f1)"
STAMP="$GBRAIN_REF $PATCH_SUM"

if [ -x "$OUTPUT_DIR/gbrain" ] && [ "$(cat "$OUTPUT_DIR/REF" 2>/dev/null)" = "$STAMP" ]; then
    echo "gbrain $GBRAIN_REF already built at $OUTPUT_DIR — skipping build."
    exit 0
fi

if ! command -v bun >/dev/null 2>&1; then
    echo "Bun is required to build gbrain (>= $MIN_BUN): https://bun.sh" >&2
    exit 1
fi
BUN_VERSION="$(bun --version)"
if [ "$(printf '%s\n%s\n' "$MIN_BUN" "$BUN_VERSION" | sort -V | head -1)" != "$MIN_BUN" ]; then
    echo "Bun $BUN_VERSION is too old to build gbrain; install Bun >= $MIN_BUN" >&2
    exit 1
fi

echo "Building gbrain $GBRAIN_REF with Bun $BUN_VERSION"
rm -rf "$SOURCE_DIR"
mkdir -p "$SOURCE_DIR"
git -C "$SOURCE_DIR" init -q
git -C "$SOURCE_DIR" remote add origin https://github.com/garrytan/gbrain.git
git -C "$SOURCE_DIR" fetch -q --depth 1 origin "$GBRAIN_REF"
git -C "$SOURCE_DIR" checkout -q FETCH_HEAD
git -C "$SOURCE_DIR" apply "$PATCH"
(cd "$SOURCE_DIR" && bun install --frozen-lockfile)

mkdir -p "$OUTPUT_DIR"
rm -f "$OUTPUT_DIR/gbrain" "$OUTPUT_DIR/REF"
(cd "$SOURCE_DIR" && bun build --compile --no-compile-autoload-bunfig \
    --outfile "$OUTPUT_DIR/gbrain" src/cli.ts)
"$OUTPUT_DIR/gbrain" --version
printf '%s\n' "$STAMP" > "$OUTPUT_DIR/REF"
echo "gbrain built at $OUTPUT_DIR/gbrain"
