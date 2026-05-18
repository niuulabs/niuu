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

# Create workspace directory if it doesn't exist
mkdir -p "$WORKSPACE_DIR"

# Configure Claude Code CLI based on provider
# Anthropic API (default)
if [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "Using Anthropic API"
    export ANTHROPIC_API_KEY
fi

# OpenAI API key (for Codex CLI)
if [ -n "$OPENAI_API_KEY" ]; then
    echo "Using OpenAI API"
    export OPENAI_API_KEY
fi

# Ollama or compatible API
if [ -n "$ANTHROPIC_BASE_URL" ]; then
    echo "Using custom API endpoint: $ANTHROPIC_BASE_URL"
    export ANTHROPIC_BASE_URL
    export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-}"
fi

# Set up Claude Code configuration directory.
# Default to $HOME/.claude where OAuth credentials live (Claude Max subscription).
# The Helm chart sets CLAUDE_CONFIG_DIR explicitly; this fallback covers direct runs.
export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$CLAUDE_CONFIG_DIR"

# Source secrets from manifest (no-op if manifest doesn't exist)
MANIFEST="/run/secrets/manifest.json"
SECRET_DIR="/run/secrets/user"
if [ -f "$MANIFEST" ] && [ -d "$SECRET_DIR" ]; then
    echo "Sourcing secrets from manifest"
    eval "$(python3 -c "
import json, os, sys
manifest = json.load(open('$MANIFEST'))
for env_var, spec in manifest.get('env', {}).items():
    fpath = os.path.join('$SECRET_DIR', spec['file'])
    if not os.path.exists(fpath):
        continue
    data = json.load(open(fpath))
    val = data.get(spec.get('key', ''), '')
    # Shell-escape single quotes
    safe = val.replace(\"'\", \"'\\\\''\" )
    print(f\"export {env_var}='{safe}'\")
for target, spec in manifest.get('files', {}).items():
    src = os.path.join('$SECRET_DIR', spec['file'])
    if os.path.exists(src):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if not os.path.exists(target):
            os.symlink(src, target)
")"
fi

# Source env vars rendered by the configured secret injector (no-op if file doesn't exist)
if [ -f /run/secrets/env.sh ]; then
    echo "Sourcing injected session secrets"
    . /run/secrets/env.sh
fi

# Remove leftover Infisical agent access token (no longer needed after init)
rm -f /home/.infisical-workdir/identity-access-token

echo "=== Starting Broker Service (port 8081) ==="
exec python -m skuld
