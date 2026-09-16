#!/usr/bin/env bash
# Stop the hand-started Travis and hand him to systemd.
# Pattern lives in a file so pgrep cannot match the caller's own argv.
set -uo pipefail
PIDS=$(pgrep -f 'ravn gateway --persona travis' || true)
if [ -n "$PIDS" ]; then
  echo "stopping hand-started travis: $PIDS"
  kill $PIDS 2>/dev/null || true
  sleep 4
  REMAIN=$(pgrep -f 'ravn gateway --persona travis' || true)
  [ -n "$REMAIN" ] && kill -9 $REMAIN 2>/dev/null || true
fi
sleep 1
systemctl --user enable --now ravn-travis.service
sleep 2
systemctl --user is-active ravn-travis.service
