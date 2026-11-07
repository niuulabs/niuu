"""Guest program parts every host-mode runtime shares (Linux slots and Macs).

A host session runs on the machine itself as its slot's user, so the steps that prepare
its storage and boot it are the same whatever the platform; only how the session is
started differs (a PID namespace in a systemd scope on Linux, a launchd job on a Mac).
Each part is source text the runtimes put in front of their own guest program.
"""

from __future__ import annotations

#: Agent CLIs a session runs from its image or bundle, ahead of everything else on PATH.
AGENT_COMMANDS = ("claude", "codex", "opencode", "grok", "pi", "muse")
#: Tools taken from the image or bundle only where neither Nix nor the machine has them.
FALLBACK_TOOLS = ("gh", "glab", "uv", "uvx", "node", "npm", "npx")

#: The session's storage, as root prepares it for the slot's user: ``owned`` creates a
#: directory the slot owns without ever following a link the slot could have left,
#: ``prepare_storage`` checks the allocation marker, gives the slot its workspace and
#: home, clones the repository as the slot's user (root never writes into storage the
#: slot owns) and hands everything in the storage to the slot, and ``require_nix``
#: refuses a session that expects Nix on a machine without its daemon.
SLOT_STORAGE = r"""
import os, pathlib, subprocess
def owned(path, mode, uid):
    parent = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.mkdir(os.path.basename(path), mode, dir_fd=parent)
        except FileExistsError:
            pass
        fd = os.open(os.path.basename(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent)
        try:
            os.fchown(fd, uid, uid)
            os.fchmod(fd, mode)
        finally:
            os.close(fd)
    finally:
        os.close(parent)
def prepare_storage(root, marker, uid, repo, branch, keep_owners=()):
    root = pathlib.Path(root)
    found = root / '.allocation'
    if not found.exists():
        raise RuntimeError('Session storage was not prepared')
    if found.read_text() != marker:
        raise RuntimeError('Workspace allocation mismatch')
    workspace, home = root / 'workspace', root / 'home'
    owned(str(workspace), 0o700, uid)
    owned(str(home), 0o700, uid)
    if repo and not os.path.lexists(str(workspace / '.git')):
        args = ['git', 'clone', *(['--branch', branch] if branch else []), '--', repo,
            str(workspace)]
        subprocess.run(args, check=True, stdout=subprocess.DEVNULL, user=uid, group=uid,
            extra_groups=[], cwd=str(home), env={'PATH': '/usr/bin:/bin', 'HOME': str(home)})
    # -h: a link the session left in its storage names its own path, never a target.
    # Container storage keeps the owners its subordinate ids gave it.
    prune = [a for path in keep_owners for a in ('-path', str(root / path), '-prune', '-o')]
    subprocess.run(['find', str(root), *prune, '-exec', 'chown', '-h', '%d:%d' % (uid, uid),
        '{}', '+'], check=True)
    return workspace, home
def require_nix(machine):
    if not os.path.exists('/nix/var/nix/daemon-socket/socket'):
        raise RuntimeError('This ' + machine + ' has no Nix daemon; install Nix through the '
            'host recipe or set compute.runtime.kwargs.nix: false')
"""

#: What a host session runs as its slot's user before Skuld, on every platform: the
#: delivered secrets become environment and files (``skuld.session_secrets``), the
#: injector's ``env.sh`` is sourced, and Skuld starts from ``NIUU_SKULD_PYTHON`` (the
#: image's relocatable Python on Linux, the bundle's on a Mac). The exports are
#: assigned before ``eval``: a failure inside the substitution would not stop ``set -e``.
SESSION_BOOT = r"""
set -eu
python=$NIUU_SKULD_PYTHON
exports=$("$python" -m skuld.session_secrets)
eval "$exports"
if [ -f "${NIUU_SECRETS_DIR:-/run/secrets}/env.sh" ]; then
    . "${NIUU_SECRETS_DIR:-/run/secrets}/env.sh"
fi
unset NIUU_SKULD_PYTHON NIUU_SESSION_LINKS NIUU_SESSION_PLACES NIUU_SECRETS_DIR
export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$CLAUDE_CONFIG_DIR" "$WORKSPACE_DIR" "${TMPDIR:-/tmp}"
exec "$python" -m skuld
"""
