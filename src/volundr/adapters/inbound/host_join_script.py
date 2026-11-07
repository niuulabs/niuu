"""The one command a host runs to join a Forge: ``curl … join.sh | sudo sh``.

Forge renders the script for the pairing code that fetched it, so the code appears once on
the operator's command line. The script is POSIX ``sh`` and needs only what a stock systemd
Linux has. It registers the host's own sshd Ed25519 key, so sshd and every operator's
``known_hosts`` are left exactly as they were. Everything else a host needs (Nix, rootless
Podman, the slot supervisor) comes from the Forge's recipe afterwards, over SSH.
"""

# The scripts below are shell, read on the host; their lines are kept whole.
# ruff: noqa: E501

from __future__ import annotations

import shlex

from volundr.adapters.outbound.host_checks import DARWIN_FACTS_SH, FACTS_SH, LEAVE_PATH

#: The host's Forge configuration, read by niuu-host-leave.
HOST_CONFIG_PATH = "/etc/niuu/compute-host.env"
#: Where Forge's guest agents read the host key: a link to sshd's own key.
NIUU_HOST_KEY_PATH = "/etc/niuu/ssh_host_ed25519_key"

_JOIN = r"""#!/bin/sh
# Join this machine to a Niuu Forge as compute. Rendered by the Forge for one pairing code;
# run it once, as root:  curl … | sudo sh
set -eu
FORGE_URL=__FORGE_URL__
CODE=__CODE__
CONFIG=__CONFIG__
LEAVE=__LEAVE__
NIUU_KEY=__NIUU_KEY__
SSHD_KEY=/etc/ssh/ssh_host_ed25519_key

say() { printf 'niuu: %s\n' "$*"; }
fail() { printf 'niuu: %s\n' "$*" >&2; exit 1; }
ok() { printf '\342\234\223 %s\n' "$*"; }
__DARWIN__
[ "$(id -u)" = 0 ] || fail "run this as root: … | sudo sh"
[ "$(uname -s)" = Linux ] || fail "only Linux and macOS hosts can join a Forge"
[ -e "$CONFIG" ] && fail "this machine already joined a Forge ($CONFIG); run $LEAVE first"

missing=""
for tool in sh ssh-keygen python3 sudo systemctl ip awk sed; do
  command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
[ -x /usr/sbin/sshd ] || command -v sshd >/dev/null 2>&1 || missing="$missing sshd"
if command -v curl >/dev/null 2>&1; then
  post() { curl -fsS --fail-with-body -H "Authorization: Bearer $CODE" \
    -H 'Content-Type: application/json' --data "$1" "$2"; }
elif command -v wget >/dev/null 2>&1; then
  post() { wget -qO- --content-on-error --header="Authorization: Bearer $CODE" \
    --header='Content-Type: application/json' --post-data="$1" "$2"; }
else
  missing="$missing curl-or-wget"
fi
[ -z "$missing" ] || fail "missing:$missing. On Debian or Ubuntu: apt install openssh-server sudo python3 iproute2 curl"
[ "$(stat -fc %T /sys/fs/cgroup 2>/dev/null)" = cgroup2fs ] || \
  fail "cgroup v2 is required (systemd.unified_cgroup_hierarchy=1); this host runs cgroup v1"
ok "Linux $(uname -r), systemd, cgroup v2, OpenSSH, python3"

# The host's identity is sshd's own Ed25519 key: nothing about sshd changes.
[ -e "$SSHD_KEY" ] || ssh-keygen -A >/dev/null
[ -e "$SSHD_KEY.pub" ] || fail "sshd has no Ed25519 host key at $SSHD_KEY"
mkdir -p /etc/niuu
chmod 0755 /etc/niuu
ln -sf "$SSHD_KEY" "$NIUU_KEY"
ln -sf "$SSHD_KEY.pub" "$NIUU_KEY.pub"
ok "host key $(ssh-keygen -lf "$SSHD_KEY.pub" | awk '{print $2}')"

NAME=$(hostname -s)
ADDRESS=$(ip -o route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([^ ]*\).*/\1/p')
FACTS=$(sh <<'NIUU_FACTS'
__FACTS__
NIUU_FACTS
)
BODY=$(NAME="$NAME" ADDRESS="$ADDRESS" FACTS="$FACTS" PUB="$(cat "$SSHD_KEY.pub")" python3 -c '
import json, os
facts = {}
for line in os.environ["FACTS"].splitlines():
    kind, _, rest = line.partition(" ")
    key, _, value = rest.partition(" ")
    if kind == "fact" and key:
        facts[key] = int(value) if value.isdigit() else {"true": True, "false": False}.get(value, value)
print(json.dumps({"name": os.environ["NAME"], "address": os.environ["ADDRESS"],
                  "public_key": os.environ["PUB"], "facts": facts}))
')

REPLY=$(post "$BODY" "$FORGE_URL/api/v1/forge/compute/hosts/join") || \
  fail "the Forge refused the join: $REPLY"

# The reply says which account Forge logs in as, its key, and what the connector needs.
eval "$(REPLY="$REPLY" python3 -c '
import json, os, shlex
r = json.loads(os.environ["REPLY"])
print("HOST_ID=" + shlex.quote(r["host_id"]))
print("HOST_NAME=" + shlex.quote(r["name"]))
print("SSH_USER=" + shlex.quote(r["ssh_user"]))
print("FORGE_KEY_LINE=" + shlex.quote(r["authorized_key_options"] + " " + r["forge_public_key"].strip() + " niuu-forge-" + r["host_id"]))
print("POOL=" + shlex.quote(r["pool_id"]))
')"

HOME_DIR=$(getent passwd "$SSH_USER" | cut -d: -f6)
[ -n "$HOME_DIR" ] || fail "the Forge logs in as $SSH_USER, which does not exist here"
if [ "$SSH_USER" = root ]; then
  case "$(sshd -T 2>/dev/null | awk '$1 == "permitrootlogin" {print $2}')" in
    no) fail "sshd has PermitRootLogin no; the Forge logs in as root with its key (prohibit-password is enough)" ;;
  esac
fi
install -d -m 0700 -o "$SSH_USER" "$HOME_DIR/.ssh"
touch "$HOME_DIR/.ssh/authorized_keys"
chown "$SSH_USER" "$HOME_DIR/.ssh/authorized_keys"
chmod 0600 "$HOME_DIR/.ssh/authorized_keys"
printf '%s\n' "$FORGE_KEY_LINE" >> "$HOME_DIR/.ssh/authorized_keys"
ok "registered as $HOST_NAME with the Forge's pool $POOL"
ok "the Forge's key installed for $SSH_USER"

REPLY="$REPLY" python3 -c '
import json, os, pathlib, subprocess
r = json.loads(os.environ["REPLY"])
for item in r["setup"]["files"]:
    path = pathlib.Path(item["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(item["content"])
    path.chmod(int(item["mode"], 8))
    print("\u2713 wrote " + str(path))
for command in r["setup"]["commands"]:
    subprocess.run(command, check=True)
' 2>&1 || fail "the connector setup failed"
SETUP_FILES=$(REPLY="$REPLY" python3 -c '
import json, os
print(" ".join(item["path"] for item in json.loads(os.environ["REPLY"])["setup"]["files"]))
')

umask 077
cat > "$CONFIG" <<EOF
FORGE_URL='$FORGE_URL'
HOST_ID='$HOST_ID'
HOST_NAME='$HOST_NAME'
SSH_USER='$SSH_USER'
SETUP_FILES="$SETUP_FILES"
EOF
umask 022
cat > "$LEAVE" <<'NIUU_LEAVE'
__LEAVE_SCRIPT__
NIUU_LEAVE
chmod 0755 "$LEAVE"

say "the Forge is preparing this host now; follow it in the UI."
say "to undo: sudo $LEAVE"
"""

_LEAVE = r"""#!/bin/sh
# Undo what joining a Niuu Forge set up: the Forge's key, the key link and this config.
# Slot users and data are the Forge's audit's business, not this script's.
set -eu
CONFIG=__CONFIG__
[ "$(id -u)" = 0 ] || { echo "niuu: run as root" >&2; exit 1; }
[ -r "$CONFIG" ] || { echo "niuu: this machine has not joined a Forge" >&2; exit 1; }
# shellcheck source=/dev/null
. "$CONFIG"
HOME_DIR=$(getent passwd "$SSH_USER" | cut -d: -f6 || true)
KEYS="$HOME_DIR/.ssh/authorized_keys"
if [ -n "$HOME_DIR" ] && [ -f "$KEYS" ]; then
  grep -v " niuu-forge-$HOST_ID\$" "$KEYS" > "$KEYS.niuu-tmp" || true
  chmod 0600 "$KEYS.niuu-tmp"
  chown "$SSH_USER" "$KEYS.niuu-tmp"
  mv "$KEYS.niuu-tmp" "$KEYS"
fi
for path in $SETUP_FILES; do rm -f "$path"; done
rm -f __NIUU_KEY__ __NIUU_KEY__.pub "$CONFIG"
echo "niuu: $HOST_NAME left the Forge at $FORGE_URL"
rm -f "$0"
"""

# A Mac takes the same steps with its own tools: launchd instead of systemd, Directory
# Services instead of getent, Remote Login as its sshd, and no cgroups to check. The Linux
# script runs it with the code and paths in the environment, so the code is in the script
# once.
_DARWIN_BRANCH = r"""if [ "$(uname -s)" = Darwin ]; then
  FORGE_URL="$FORGE_URL" CODE="$CODE" CONFIG="$CONFIG" LEAVE="$LEAVE" NIUU_KEY="$NIUU_KEY" \
    sh -s <<'NIUU_JOIN_DARWIN'
__DARWIN_JOIN__
NIUU_JOIN_DARWIN
  exit
fi
"""

_JOIN_DARWIN = r"""set -eu
SSHD_KEY=/etc/ssh/ssh_host_ed25519_key

say() { printf 'niuu: %s\n' "$*"; }
fail() { printf 'niuu: %s\n' "$*" >&2; exit 1; }
ok() { printf '\342\234\223 %s\n' "$*"; }
home_of() { dscl . -read "/Users/$1" NFSHomeDirectory 2>/dev/null | awk '{print $2}'; }

[ "$(id -u)" = 0 ] || fail "run this as root: … | sudo sh"
[ "$(uname -s)" = Darwin ] || fail "this is the macOS join; run the script the Forge serves"
[ -e "$CONFIG" ] && fail "this Mac already joined a Forge ($CONFIG); run $LEAVE first"

missing=""
for tool in sh ssh-keygen python3 sudo launchctl dscl dseditgroup route ipconfig sysctl \
    sw_vers awk sed; do
  command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
[ -x /usr/sbin/sshd ] || command -v sshd >/dev/null 2>&1 || missing="$missing sshd"
if command -v curl >/dev/null 2>&1; then
  post() { curl -fsS --fail-with-body -H "Authorization: Bearer $CODE" \
    -H 'Content-Type: application/json' --data "$1" "$2"; }
else
  missing="$missing curl"
fi
[ -z "$missing" ] || fail "missing:$missing; these come with macOS and its Command Line Tools (xcode-select --install)"
# /usr/bin/python3 is a stub until the Command Line Tools are installed.
python3 -c 'import json' >/dev/null 2>&1 || \
  fail "python3 does not run; install Apple's Command Line Tools: xcode-select --install"
launchctl print system/com.openssh.sshd >/dev/null 2>&1 || \
  fail "Remote Login is off; turn it on in System Settings > General > Sharing > Remote Login"
ok "macOS $(sw_vers -productVersion), launchd, Remote Login, python3"

# The host's identity is sshd's own Ed25519 key: nothing about sshd changes.
[ -e "$SSHD_KEY" ] || ssh-keygen -A >/dev/null
[ -e "$SSHD_KEY.pub" ] || fail "sshd has no Ed25519 host key at $SSHD_KEY"
mkdir -p /etc/niuu
chmod 0755 /etc/niuu
ln -sf "$SSHD_KEY" "$NIUU_KEY"
ln -sf "$SSHD_KEY.pub" "$NIUU_KEY.pub"
ok "host key $(ssh-keygen -lf "$SSHD_KEY.pub" | awk '{print $2}')"

NAME=$(hostname -s)
IFACE=$(route -n get 1.1.1.1 2>/dev/null | awk '/interface:/ {print $2}')
ADDRESS=$(ipconfig getifaddr "$IFACE" 2>/dev/null || true)
FACTS=$(sh <<'NIUU_FACTS'
__FACTS__
NIUU_FACTS
)
BODY=$(NAME="$NAME" ADDRESS="$ADDRESS" FACTS="$FACTS" PUB="$(cat "$SSHD_KEY.pub")" python3 -c '
import json, os
facts = {}
for line in os.environ["FACTS"].splitlines():
    kind, _, rest = line.partition(" ")
    key, _, value = rest.partition(" ")
    if kind == "fact" and key:
        facts[key] = int(value) if value.isdigit() else {"true": True, "false": False}.get(value, value)
print(json.dumps({"name": os.environ["NAME"], "address": os.environ["ADDRESS"],
                  "public_key": os.environ["PUB"], "facts": facts}))
')

REPLY=$(post "$BODY" "$FORGE_URL/api/v1/forge/compute/hosts/join") || \
  fail "the Forge refused the join: $REPLY"

eval "$(REPLY="$REPLY" python3 -c '
import json, os, shlex
r = json.loads(os.environ["REPLY"])
print("HOST_ID=" + shlex.quote(r["host_id"]))
print("HOST_NAME=" + shlex.quote(r["name"]))
print("SSH_USER=" + shlex.quote(r["ssh_user"]))
print("FORGE_KEY_LINE=" + shlex.quote(r["authorized_key_options"] + " " + r["forge_public_key"].strip() + " niuu-forge-" + r["host_id"]))
print("POOL=" + shlex.quote(r["pool_id"]))
')"

HOME_DIR=$(home_of "$SSH_USER")
[ -n "$HOME_DIR" ] || fail "the Forge logs in as $SSH_USER, which does not exist here"
# Remote Login set to "Only these users" keeps everyone else out, the Forge included.
if dscl . -read /Groups/com.apple.access_ssh >/dev/null 2>&1; then
  dseditgroup -o checkmember -m "$SSH_USER" com.apple.access_ssh >/dev/null 2>&1 || \
    fail "Remote Login admits only some users, not $SSH_USER, whom the Forge logs in as; add it under Remote Login, or allow all users"
fi
if [ "$SSH_USER" = root ]; then
  case "$(/usr/sbin/sshd -T 2>/dev/null | awk '$1 == "permitrootlogin" {print $2}')" in
    no) fail "sshd has PermitRootLogin no; the Forge logs in as root with its key (prohibit-password is enough)" ;;
  esac
fi
install -d -m 0700 -o "$SSH_USER" "$HOME_DIR/.ssh"
touch "$HOME_DIR/.ssh/authorized_keys"
chown "$SSH_USER" "$HOME_DIR/.ssh/authorized_keys"
chmod 0600 "$HOME_DIR/.ssh/authorized_keys"
printf '%s\n' "$FORGE_KEY_LINE" >> "$HOME_DIR/.ssh/authorized_keys"
ok "registered as $HOST_NAME with the Forge's pool $POOL"
ok "the Forge's key installed for $SSH_USER"

REPLY="$REPLY" python3 -c '
import json, os, pathlib, subprocess
r = json.loads(os.environ["REPLY"])
for item in r["setup"]["files"]:
    path = pathlib.Path(item["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(item["content"])
    path.chmod(int(item["mode"], 8))
    print("✓ wrote " + str(path))
for command in r["setup"]["commands"]:
    subprocess.run(command, check=True)
' 2>&1 || fail "the connector setup failed"
SETUP_FILES=$(REPLY="$REPLY" python3 -c '
import json, os
print(" ".join(item["path"] for item in json.loads(os.environ["REPLY"])["setup"]["files"]))
')

umask 077
cat > "$CONFIG" <<EOF
FORGE_URL='$FORGE_URL'
HOST_ID='$HOST_ID'
HOST_NAME='$HOST_NAME'
SSH_USER='$SSH_USER'
SETUP_FILES="$SETUP_FILES"
EOF
umask 022
mkdir -p "$(dirname "$LEAVE")"
cat > "$LEAVE" <<'NIUU_LEAVE'
__LEAVE_SCRIPT__
NIUU_LEAVE
chmod 0755 "$LEAVE"

say "the Forge is preparing this Mac now; follow it in the UI."
say "to undo: sudo $LEAVE"
"""

_LEAVE_DARWIN = r"""#!/bin/sh
# Undo what joining a Niuu Forge set up on this Mac: the Forge's key, the key link and this
# config. The slot user and its data are the Forge's audit's business, not this script's.
set -eu
CONFIG=__CONFIG__
[ "$(id -u)" = 0 ] || { echo "niuu: run as root" >&2; exit 1; }
[ -r "$CONFIG" ] || { echo "niuu: this Mac has not joined a Forge" >&2; exit 1; }
# shellcheck source=/dev/null
. "$CONFIG"
HOME_DIR=$(dscl . -read "/Users/$SSH_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}' || true)
KEYS="$HOME_DIR/.ssh/authorized_keys"
if [ -n "$HOME_DIR" ] && [ -f "$KEYS" ]; then
  grep -v " niuu-forge-$HOST_ID\$" "$KEYS" > "$KEYS.niuu-tmp" || true
  chmod 0600 "$KEYS.niuu-tmp"
  chown "$SSH_USER" "$KEYS.niuu-tmp"
  mv "$KEYS.niuu-tmp" "$KEYS"
fi
for path in $SETUP_FILES; do rm -f "$path"; done
rm -f __NIUU_KEY__ __NIUU_KEY__.pub "$CONFIG"
echo "niuu: $HOST_NAME left the Forge at $FORGE_URL"
rm -f "$0"
"""


def render_leave_script() -> str:
    return _LEAVE.replace("__CONFIG__", shlex.quote(HOST_CONFIG_PATH)).replace(
        "__NIUU_KEY__", shlex.quote(NIUU_HOST_KEY_PATH)
    )


def render_darwin_leave_script() -> str:
    return _LEAVE_DARWIN.replace("__CONFIG__", shlex.quote(HOST_CONFIG_PATH)).replace(
        "__NIUU_KEY__", shlex.quote(NIUU_HOST_KEY_PATH)
    )


def render_darwin_join_script() -> str:
    """The Mac's steps; the Linux script runs them with its code and paths in the env."""
    return _JOIN_DARWIN.replace("__FACTS__", DARWIN_FACTS_SH.strip()).replace(
        "__LEAVE_SCRIPT__", render_darwin_leave_script()
    )


def render_join_script(*, forge_url: str, code: str) -> str:
    """The join script for one code; ``code`` is the caller's own bearer, echoed back."""
    return (
        _JOIN.replace(
            "__DARWIN__", _DARWIN_BRANCH.replace("__DARWIN_JOIN__", render_darwin_join_script())
        )
        .replace("__FORGE_URL__", shlex.quote(forge_url))
        .replace("__CODE__", shlex.quote(code))
        .replace("__CONFIG__", shlex.quote(HOST_CONFIG_PATH))
        .replace("__LEAVE__", shlex.quote(LEAVE_PATH))
        .replace("__NIUU_KEY__", shlex.quote(NIUU_HOST_KEY_PATH))
        .replace("__FACTS__", FACTS_SH.strip())
        .replace("__LEAVE_SCRIPT__", render_leave_script())
    )
