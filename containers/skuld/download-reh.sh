#!/usr/bin/env bash
# Download VS Code Remote Extension Host (REH) binary matching the
# @codingame/monaco-vscode-api npm package version.
#
# Usage: ./download-reh.sh [output-dir]
#
# The Microsoft release commit is fetched from the CodinGame GitHub
# source (npm strips the config field during publish). This commit
# is the only one Microsoft publishes REH binaries for.

set -euo pipefail

OUTPUT_DIR="${1:-/opt/vscode-reh}"
ARCH="${ARCH:-linux-x64}"
PACKAGE_JSON="node_modules/@codingame/monaco-vscode-api/package.json"

if [ ! -f "$PACKAGE_JSON" ]; then
  echo "ERROR: $PACKAGE_JSON not found." >&2
  echo "Install @codingame/monaco-vscode-api in the build context first." >&2
  exit 1
fi

# npm strips the config field during publish, so we read the package
# version and fetch the commit from the GitHub source package.json.
VERSION=$(jq -r '.version' "$PACKAGE_JSON")
if [ -z "$VERSION" ] || [ "$VERSION" = "null" ]; then
  echo "ERROR: Could not read version from $PACKAGE_JSON" >&2
  exit 1
fi

echo "Package version: $VERSION"
echo "Fetching VS Code commit from GitHub source..."

GITHUB_URL="https://raw.githubusercontent.com/CodinGame/monaco-vscode-api/v${VERSION}/package.json"
COMMIT=$(curl -fsSL "$GITHUB_URL" | jq -r '.config.vscode.commit // empty')
if [ -z "$COMMIT" ]; then
  echo "ERROR: Could not read .config.vscode.commit from $GITHUB_URL" >&2
  exit 1
fi

echo "VS Code commit: $COMMIT"
echo "Architecture:   $ARCH"
echo "Output:         $OUTPUT_DIR"

mkdir -p "$OUTPUT_DIR"

URL="https://update.code.visualstudio.com/commit:${COMMIT}/server-${ARCH}/stable"
echo "Downloading from: $URL"

curl -fsSL "$URL" -o /tmp/reh.tar.gz
tar -xz -C "$OUTPUT_DIR" --strip-components=1 -f /tmp/reh.tar.gz
rm -f /tmp/reh.tar.gz

echo "REH server installed to $OUTPUT_DIR"
echo "Start with: $OUTPUT_DIR/bin/code-server-oss --host 0.0.0.0 --port 8445 --without-connection-token"
