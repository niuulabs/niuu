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
#: refuses a session that expects Nix on a machine without its daemon. With ``git`` (the
#: attached integration's username, its token as root read it, and the settings the
#: session's own Git reads it with) the clone reads the token from its environment
#: through a repository-scoped credential helper: never a URL, the config or a file.
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
def prepare_storage(root, marker, uid, repo, branch, keep_owners=(), git=None):
    root = pathlib.Path(root)
    found = root / '.allocation'
    if not found.exists():
        raise RuntimeError('Session storage was not prepared')
    if found.read_text() != marker:
        raise RuntimeError('Workspace allocation mismatch')
    workspace, home = root / 'workspace', root / 'home'
    owned(str(workspace), 0o700, uid)
    owned(str(home), 0o700, uid)
    as_slot = {'user': uid, 'group': uid, 'extra_groups': [], 'cwd': str(home),
        'stdout': subprocess.DEVNULL, 'check': True}
    plain = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'GIT_TERMINAL_PROMPT': '0'}
    if repo and not os.path.lexists(str(workspace / '.git')):
        config, env = [], dict(plain)
        if git:
            config = ['-c', 'credential.helper=', '-c', 'credential.useHttpPath=true',
                '-c', 'credential.' + repo + '.helper=!f() { [ "$1" = get ] || return 0; '
                '[ -n "$NIUU_GIT_TOKEN" ] || return 1; printf "username=%s\\npassword=%s\\n" '
                '"$NIUU_GIT_USERNAME" "$NIUU_GIT_TOKEN"; }; f']
            env.update(NIUU_GIT_USERNAME=git['username'], NIUU_GIT_TOKEN=git['token'])
        args = ['git', *config, 'clone', *(['--branch', branch] if branch else []), '--', repo,
            str(workspace)]
        subprocess.run(args, env=env, **as_slot)
    # -h: a link the session left in its storage names its own path, never a target.
    # Container storage keeps the owners its subordinate ids gave it. A restored
    # workspace is root's until here (the restore drops the archive's owners).
    prune = [a for path in keep_owners for a in ('-path', str(root / path), '-prune', '-o')]
    subprocess.run(['find', str(root), *prune, '-exec', 'chown', '-h', '%d:%d' % (uid, uid),
        '{}', '+'], check=True)
    if repo and git:
        # What the session's own Git reads the token with, on every fetch and push:
        # written as the slot, into a repository the slot now owns, cloned or restored.
        for key, value in git['settings']:
            subprocess.run(['git', 'config', '--file', str(workspace / '.git' / 'config'),
                key, value], env=plain, **as_slot)
    return workspace, home
def integration_git(g, repo, token, token_file):
    # The attached integration's token as root reads it (the agent writes it as its own
    # user), for the clone's environment, and the settings the session's own Git reads the
    # file at token_file with, on every fetch and push.
    if not token.is_file() or not token.stat().st_size:
        raise RuntimeError('The Git integration token was not rendered at ' + token_file)
    settings = [('niuu.gitTokenFile', token_file), ('niuu.gitUsername', g['username']),
        ('credential.helper', ''), ('credential.useHttpPath', 'true'),
        ('credential.' + repo + '.helper', '!f() { [ "$1" = get ] || return 0; '
         'token=$(cat "$(git config niuu.gitTokenFile)") || return 1; '
         '[ -n "$token" ] || return 1; printf "username=%s\\npassword=%s\\n" '
         '"$(git config niuu.gitUsername)" "$token"; }; f')]
    settings += [(k, g[v]) for k, v in (('user.name', 'user_name'),
        ('user.email', 'user_email'), ('niuu.baseBranch', 'base_branch')) if g[v]]
    return {'username': g['username'], 'token': token.read_text().strip(),
        'settings': settings}
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
