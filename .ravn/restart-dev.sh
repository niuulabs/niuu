#!/usr/bin/env bash
# Restart the dev Ravn gateway.
#
# Matching is done on the CONFIG FILE PATH rather than a name fragment, and via
# a script file rather than an inline command, because `pkill -f lexi-dev` run
# from an interactive shell matches that shell's own argv and kills the caller.
set -uo pipefail
PATTERN='config-lexi-dev.yaml'
PIDS=$(pgrep -f "$PATTERN" | grep -v "^$$\$" || true)
if [ -n "$PIDS" ]; then
  echo "stopping: $PIDS"
  kill $PIDS 2>/dev/null || true
  sleep 3
  kill -9 $(pgrep -f "$PATTERN" || true) 2>/dev/null || true
fi
mkdir -p "$HOME/.ravn-dev"
setsid nohup "$HOME/.ravn/start-lexi-dev.sh" >> "$HOME/.ravn-dev/lexi-dev.log" 2>&1 < /dev/null &
echo "started"
