#!/bin/bash
set -e

# Skuld Entrypoint Script
# Prepares the workspace and starts the broker service (Claude Code + Codex)

echo "==== Skuld Entrypoint ===="
echo "Session ID: ${SESSION_ID:-unknown}"
echo "Workspace: ${WORKSPACE_DIR:-/volundr/sessions/${SESSION_ID}/workspace}"

# Set defaults
export SESSION_ID="${SESSION_ID:-unknown}"
export WORKSPACE_DIR="${WORKSPACE_DIR:-/volundr/sessions/${SESSION_ID}/workspace}"

# The delivered secrets become environment and files (skuld.session_secrets, the one
# program every session runtime boots with). Assigned first: a failure inside eval's
# command substitution would not stop the script under set -e.
exports=$(python -m skuld.session_secrets)
eval "$exports"

# Source env vars rendered by the configured secret injector (no-op if file doesn't exist)
if [ -f /run/secrets/env.sh ]; then
    echo "Sourcing injected session secrets"
    . /run/secrets/env.sh
fi

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Broker-only filesystem and CLI setup. Explicit commands (including sealed
# credential enrollment) must remain read-only safe.
mkdir -p "$WORKSPACE_DIR"

if [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "Using Anthropic API"
    export ANTHROPIC_API_KEY
fi

if [ -n "$OPENAI_API_KEY" ]; then
    echo "Using OpenAI API"
    export OPENAI_API_KEY
fi

if [ -n "$ANTHROPIC_BASE_URL" ]; then
    echo "Using custom API endpoint: $ANTHROPIC_BASE_URL"
    export ANTHROPIC_BASE_URL
    export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-}"
fi

export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$CLAUDE_CONFIG_DIR"

# Remove leftover Infisical agent access token (no longer needed after init)
rm -f /home/.infisical-workdir/identity-access-token

echo "=== Starting Broker Service (port 8081) ==="
exec python -m skuld
