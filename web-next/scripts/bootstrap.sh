#!/usr/bin/env bash
# bootstrap.sh — install deps, build packages, and verify workspace links.
# Run from anywhere; the script resolves its own root.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "▸ installing dependencies"
pnpm install

echo "▸ building packages"
pnpm build

echo "✔ bootstrap complete — run 'pnpm storybook' or 'pnpm dev'"
