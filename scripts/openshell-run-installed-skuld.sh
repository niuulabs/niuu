#!/bin/sh
set -eu

LOG_FILE="${SKULD_BOOTSTRAP_LOG:-/tmp/skuld.log}"
PORT="${SKULD__PORT:-8081}"
HEALTH_URL="http://127.0.0.1:${PORT}/health"

if [ -n "${SKULD_PYTHON_BIN:-}" ]; then
    PYTHON_BIN="$SKULD_PYTHON_BIN"
elif [ -x /opt/niuu/bin/python ]; then
    PYTHON_BIN=/opt/niuu/bin/python
else
    PYTHON_BIN="$(command -v python)"
fi

setup_cli_home() {
    env_name="$1"
    default_path="$2"
    writable_path="$3"
    current_value="$(eval "printf '%s' \"\${${env_name}:-}\"")"
    if [ -n "$current_value" ]; then
        mkdir -p "$current_value"
        return
    fi
    if [ -d "$default_path" ]; then
        mkdir -p "$writable_path"
        cp -R "$default_path/." "$writable_path/" 2>/dev/null || true
        export "${env_name}=$writable_path"
        return
    fi
    mkdir -p "$default_path"
    export "${env_name}=$default_path"
}

setup_cli_home CODEX_HOME "$HOME/.codex" /tmp/codex-home
setup_cli_home CLAUDE_CONFIG_DIR "$HOME/.claude" /tmp/claude-home
"$PYTHON_BIN" -m skuld.openshell_home

nohup "$PYTHON_BIN" -m skuld >"$LOG_FILE" 2>&1 &
broker_pid="$!"

i=0
while [ "$i" -lt "${SKULD_BOOTSTRAP_TIMEOUT_SECONDS:-90}" ]; do
    if ! kill -0 "$broker_pid" 2>/dev/null; then
        tail -n 120 "$LOG_FILE" >&2 || true
        exit 1
    fi
    if "$PYTHON_BIN" - "$HEALTH_URL" <<'PY'
import sys
import urllib.request

url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=1) as response:
        raise SystemExit(0 if response.status < 500 else 1)
except Exception:
    raise SystemExit(1)
PY
    then
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done

tail -n 120 "$LOG_FILE" >&2 || true
exit 1
