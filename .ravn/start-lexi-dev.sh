#!/usr/bin/env bash
# Start the DEV Ravn — the iteration target for the LexiChat OpenClaw adapter.
#
#   ~/.ravn/start-lexi-dev.sh              # foreground
#   setsid nohup ~/.ravn/start-lexi-dev.sh >> ~/.ravn-dev/lexi-dev.log 2>&1 &   # detached
#
# Stop with:  pkill -f 'config-lexi-dev'
#
# This is NOT Travis. Telegram is disabled in config-lexi-dev.yaml and the
# bot-token env var is deliberately NOT sourced here, so even a config mistake
# cannot start a second long-poller against Damien's real thread.
set -euo pipefail

cd /home/thor/repos/niuu-dev-integration

# This box exports a live Forge session's environment. SKULD__TRANSPORT_ADAPTER
# is read by RuntimeExecutorConfig as an alias, which silently reroutes every
# turn through a Claude Code subprocess instead of the Anthropic API — the turn
# still answers, so the substitution is invisible unless you look for it.
while read -r var; do unset "$var"; done < <(
  env | grep -oE '^(SKULD__[A-Z_0-9]*|VOLUNDR[A-Z_0-9]*)' || true
)

set -a
. "$HOME/.openclaw/secrets/anthropic.env"
. "$HOME/.openclaw/secrets/ravn-openclaw.env"
set +a

# Belt and braces: if anything else in the environment carries the Telegram
# token, drop it. An unset token makes the channel log an error and skip.
unset RAVN_TELEGRAM_BOT_TOKEN || true

mkdir -p "$HOME/.ravn-dev"

exec uv run ravn gateway \
  --config "$HOME/.ravn/config-lexi-dev.yaml" \
  --persona travis
