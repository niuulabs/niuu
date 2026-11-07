"""Programs the darwin host runtime runs on a Mac, as root, over its pinned SSH connection.

Each is passed as a fixed program; whatever a session controls arrives as JSON on stdin or
in an argument. ``INSTALL`` runs under the Mac's own ``python3`` (Apple's 3.9 from the
Command Line Tools), because it is what brings the bundle's Python; every other program
runs under the bundle's own Python.

The layout matches the darwin audit (``host_checks.DARWIN_AUDIT_SH``): user and group
``niuu-slot-0`` with uid ``uid_base``, the session's storage in ``/var/lib/niuu/slots/0``,
what root writes for it in ``/var/run/niuu/slots/0``, and the session's launchd job
``world.niuu.slot.0`` in the system domain.
"""

from __future__ import annotations

#: Fetch the session bundle by digest from its OCI registry and unpack it once per digest.
#: argv: the digest-pinned reference, the bundle root, the layer media type. Prints the
#: bundle's ``bundle.json``. Anonymous registry pulls only: the bundle is public.
INSTALL = r"""
import hashlib, json, os, pathlib, re, shutil, subprocess, sys, tempfile
reference, cache, media_type = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3]
match = re.match(r'^([^/]+)/(.+)@sha256:([a-f0-9]{64})$', reference)
if not match:
    raise RuntimeError('The session bundle must be pinned by digest: ' + reference)
registry, repository, digest = match.groups()
root = cache / digest
if (root / 'bundle.json').exists():
    print((root / 'bundle.json').read_text())
    sys.exit(0)
cache.mkdir(parents=True, exist_ok=True)
os.chmod(str(cache), 0o755)
work = pathlib.Path(tempfile.mkdtemp(dir=str(cache), prefix='.fetch-'))
def curl(url, token, accept, output):
    headers = ['-H', 'Accept: ' + accept] if accept else []
    if token:
        headers += ['-H', 'Authorization: Bearer ' + token]
    return subprocess.run(['curl', '--silent', '--show-error', '--location', '--retry', '3',
        '--output', str(output), '--write-out', '%{http_code}', '--dump-header',
        str(output) + '.headers', *headers, url], check=True, capture_output=True,
        text=True).stdout.strip()
def token_for(headers):
    # The registry's Bearer challenge names where an anonymous pull token comes from.
    challenge = ''
    for line in headers.read_text().splitlines():
        if line.lower().startswith('www-authenticate:'):
            challenge = line.split(':', 1)[1].strip()
    if not challenge.lower().startswith('bearer '):
        raise RuntimeError('The registry ' + registry + ' asks for no bearer token: ' + challenge)
    fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    query = '&'.join(k + '=' + fields[k] for k in ('service', 'scope') if k in fields)
    out = work / 'token.json'
    status = curl(fields['realm'] + ('?' + query if query else ''), '', '', out)
    if status != '200':
        raise RuntimeError('The registry ' + registry + ' refused an anonymous pull token '
            '(HTTP ' + status + '); the session bundle must be public')
    answer = json.loads(out.read_text())
    return answer.get('token') or answer['access_token']
def fetch(path, accept, expected):
    url = 'https://' + registry + '/v2/' + repository + '/' + path
    out = work / ('blob-' + expected)
    status = curl(url, '', accept, out)
    if status == '401':
        status = curl(url, token_for(pathlib.Path(str(out) + '.headers')), accept, out)
    if status != '200':
        raise RuntimeError('Fetching ' + url + ' answered HTTP ' + status)
    sha = hashlib.sha256()
    with out.open('rb') as data:
        for chunk in iter(lambda: data.read(1 << 20), b''):
            sha.update(chunk)
    if sha.hexdigest() != expected:
        raise RuntimeError('The registry served ' + url + ' with digest ' + sha.hexdigest()
            + ', not ' + expected)
    return out
try:
    manifest = json.loads(fetch('manifests/sha256:' + digest,
        'application/vnd.oci.image.manifest.v1+json', digest).read_text())
    layers = [l for l in manifest.get('layers', []) if l.get('mediaType') == media_type]
    if len(layers) != 1:
        raise RuntimeError(reference + ' holds ' + str(len(layers)) + ' layers of type '
            + media_type + '; a session bundle has exactly one')
    layer = layers[0]['digest'].split(':', 1)[1]
    archive = fetch('blobs/sha256:' + layer, '', layer)
    unpacked = work / 'bundle'
    unpacked.mkdir()
    subprocess.run(['tar', '-xzf', str(archive), '-C', str(unpacked)], check=True)
    os.unlink(str(archive))
    description = json.loads((unpacked / 'bundle.json').read_text())
    platform = 'darwin-' + os.uname().machine
    if description.get('format') != 1 or description.get('platform') != platform:
        raise RuntimeError(reference + ' is a bundle for ' + str(description.get('platform'))
            + ', not ' + platform)
    # Readable by the slot user, writable by root alone.
    subprocess.run(['chown', '-R', '-h', '0:0', str(unpacked)], check=True)
    subprocess.run(['chmod', '-R', 'go-w', str(unpacked)], check=True)
    os.chmod(str(unpacked), 0o755)
    os.rename(str(unpacked), str(root))
except OSError:
    # A concurrent install of the same digest finished first.
    if not (root / 'bundle.json').exists():
        raise
finally:
    shutil.rmtree(str(work), ignore_errors=True)
print((root / 'bundle.json').read_text())
"""

#: Remove every unpacked bundle but the current one, while no session job is loaded.
#: argv: the bundle root, the current digest, the session job's label.
PRUNE = r"""
import pathlib, shutil, subprocess, sys
cache, current, label = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
loaded = subprocess.run(['launchctl', 'print', 'system/' + label], capture_output=True)
if loaded.returncode == 0 or not cache.is_dir():
    sys.exit(0)
for entry in cache.iterdir():
    if entry.name != current:
        shutil.rmtree(str(entry))
"""

#: Shared by the slot programs: the slot's processes, launchd jobs and leftovers.
_SLOT_COMMON = r"""
import json, os, pathlib, plistlib, signal, subprocess, sys, time
STAGE, DETAIL = 'NIUU_SLOT_STAGE:', 'NIUU_SLOT_DETAIL:'
def fail(stage, detail):
    print(STAGE + stage, file=sys.stderr)
    print(DETAIL + detail, file=sys.stderr)
    sys.exit(1)
def loaded(label):
    return subprocess.run(['launchctl', 'print', 'system/' + label],
        capture_output=True).returncode == 0
def bootout(label, timeout, poll):
    # launchd sends SIGTERM, then SIGKILL after the job's ExitTimeOut.
    if loaded(label):
        subprocess.run(['launchctl', 'bootout', 'system/' + label], capture_output=True)
    deadline = time.monotonic() + timeout
    while loaded(label) and time.monotonic() < deadline:
        time.sleep(poll)
    if loaded(label):
        raise RuntimeError('launchd job ' + label + ' is still loaded after bootout')
def processes(uid):
    out = subprocess.run(['ps', '-axo', 'uid=,pid='], check=True, capture_output=True,
        text=True).stdout.split()
    return [int(pid) for owner, pid in zip(out[::2], out[1::2]) if int(owner) == uid]
def write_plist(path, job):
    path = pathlib.Path(path)
    temporary = path.with_name('.' + path.name)
    with temporary.open('wb') as out:
        plistlib.dump(job, out)
    os.chmod(str(temporary), 0o644)
    os.chown(str(temporary), 0, 0)
    os.replace(str(temporary), str(path))
"""

#: The slot's user, group and directories. argv: ``up`` or ``down``. Stdin: the slot spec.
#: ``down`` ends everything the user ran, removes everything it owned and the user itself,
#: then runs the darwin audit for the slot and fails naming whatever is left.
SLOT = (
    _SLOT_COMMON
    + r"""
action, s = sys.argv[1], json.load(sys.stdin)
user, uid = s['name'], s['uid']
def ds(*args, check=True):
    return subprocess.run(['dscl', '.', *args], check=check, capture_output=True, text=True)
def record(kind, name):
    r = ds('-read', '/' + kind + '/' + name, 'UniqueID' if kind == 'Users' else 'PrimaryGroupID',
        check=False)
    if r.returncode:
        return None
    return int(r.stdout.split()[-1])
def directory(path, owner, group, mode):
    path = pathlib.Path(path)
    if path.is_symlink():
        raise RuntimeError(str(path) + ' is a link')
    path.mkdir(parents=True, exist_ok=True)
    os.chown(str(path), owner, group)
    os.chmod(str(path), mode)
if action == 'up':
    for kind in ('Groups', 'Users'):
        existing = record(kind, user)
        if existing is not None and existing != uid:
            fail('slot-up', kind[:-1].lower() + ' ' + user + ' exists with id ' + str(existing)
                + ', not ' + str(uid) + '; remove it or move uid_base')
    taken = ds('-search', '/Users', 'UniqueID', str(uid), check=False).stdout.split()
    if taken and taken[0] != user:
        fail('slot-up', 'uid ' + str(uid) + ' belongs to ' + taken[0] + '; move uid_base')
    if record('Groups', user) is None:
        ds('-create', '/Groups/' + user)
        ds('-create', '/Groups/' + user, 'PrimaryGroupID', str(uid))
        ds('-create', '/Groups/' + user, 'RealName', 'Niuu session slot ' + str(s['slot']))
        ds('-create', '/Groups/' + user, 'Password', '*')
    directory(s['data_root'], 0, 0, 0o755)
    directory(s['session_data'], 0, 0, 0o755)
    directory(s['home'], uid, uid, 0o700)
    if record('Users', user) is None:
        ds('-create', '/Users/' + user)
        for key, value in (('UniqueID', str(uid)), ('PrimaryGroupID', str(uid)),
                ('UserShell', '/bin/zsh'), ('NFSHomeDirectory', s['home']),
                ('RealName', 'Niuu session slot ' + str(s['slot'])), ('Password', '*'),
                ('IsHidden', '1')):
            ds('-create', '/Users/' + user, key, value)
    directory(s['run_root'], 0, uid, 0o750)
    directory(s['tmp'], uid, uid, 0o700)
    subprocess.run(['dscacheutil', '-flushcache'], check=True)
    sys.exit(0)
if action != 'down':
    raise RuntimeError('Unknown slot action ' + action)
timeout, poll = s['stop_timeout'], s['poll']
for label in s['labels']:
    bootout(label, timeout, poll)
    pathlib.Path('/Library/LaunchDaemons/' + label + '.plist').unlink(missing_ok=True)
for domain in ('gui/', 'user/'):
    # launchd keeps a user's domain after its last process.
    subprocess.run(['launchctl', 'bootout', domain + str(uid)], capture_output=True)
deadline = time.monotonic() + timeout
while processes(uid) and time.monotonic() < deadline:
    for pid in processes(uid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(poll)
left = processes(uid)
if left:
    fail('slot-down', 'processes ' + ', '.join(map(str, left)) + ' of uid ' + str(uid)
        + ' survived SIGKILL')
ipcs = subprocess.run(['ipcs', '-a'], check=True, capture_output=True, text=True).stdout
for line in ipcs.splitlines():
    fields = line.split()
    if len(fields) >= 8 and fields[0] in ('m', 'q', 's') and (
            user in fields[4:8] or str(uid) in fields[4:8]):
        subprocess.run(['ipcrm', '-' + fields[0], fields[1]], check=True)
pathlib.Path('/private/var/at/tabs/' + user).unlink(missing_ok=True)
for path in ('/nix/var/nix/profiles/per-user/' + user, '/nix/var/nix/gcroots/per-user/' + user):
    subprocess.run(['rm', '-rf', path], check=True)
for link in s['links']:
    path = pathlib.Path(link)
    if path.is_symlink():
        path.unlink()
for base in ('/private/tmp', '/private/var/tmp', '/private/var/folders', '/Users/Shared'):
    # Per-user temporary directories and caches outlive the user.
    if os.path.isdir(base):
        subprocess.run(['find', base, '-xdev', '-uid', str(uid), '-prune', '-exec', 'rm', '-rf',
            '{}', '+'], check=True)
for path in (s['data_root'], s['run_root'], s['state_root']):
    subprocess.run(['rm', '-rf', path], check=True)
if s['metadata_address']:
    shown = subprocess.run(['ifconfig', 'lo0', 'inet'], check=True, capture_output=True,
        text=True).stdout
    if ' ' + s['metadata_address'] + ' ' in shown:
        subprocess.run(['ifconfig', 'lo0', '-alias', s['metadata_address']], check=True)
if record('Users', user) is not None:
    ds('-delete', '/Users/' + user)
if record('Groups', user) is not None:
    ds('-delete', '/Groups/' + user)
subprocess.run(['dscacheutil', '-flushcache'], check=True)
audit = subprocess.run(['sh', '-s', '--', str(s['uid_base']), '1', '', '0', 'check'],
    input=s['audit'], check=True, capture_output=True, text=True).stdout
findings = [line[len('finding '):] for line in audit.splitlines() if line.startswith('finding ')]
if findings:
    fail('slot-down', '; '.join(findings))
"""
)

#: Start (or keep) the slot's session. Stdin: the placed launch payload with its ``darwin``
#: section. Runs the native init containers, loads the sidecars and the session as launchd
#: jobs, and exits 0 once the session's process runs.
START = (
    _SLOT_COMMON
    + r"""
p = json.load(sys.stdin)
s, d = p['slot'], p['darwin']
uid, label = s['uid'], d['label']
def running():
    shown = subprocess.run(['launchctl', 'print', 'system/' + label], capture_output=True,
        text=True)
    if shown.returncode:
        return None
    for line in shown.stdout.splitlines():
        key, _, value = line.strip().partition(' = ')
        if key == 'pid' and value.isdigit():
            return int(value)
    return None
if loaded(label):
    if not p.get('relaunch') and running():
        sys.exit(0)
    # A new launch replaced the Forge session credential the running broker read.
    bootout(label, d['stop_timeout'], d['poll'])
def owned(path, mode):
    # The slot owns the parent and could have left a link here: never follow one.
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
root = pathlib.Path(s['data_root'])
marker = root / '.allocation'
if not marker.exists():
    raise RuntimeError('Session storage was not prepared')
if marker.read_text() != s['marker']:
    raise RuntimeError('Workspace allocation mismatch')
workspace, home = root / 'workspace', root / 'home'
owned(str(workspace), 0o700)
owned(str(home), 0o700)
if p['repo'] and not os.path.lexists(str(workspace / '.git')):
    # As the slot's user: root never writes into storage the slot owns.
    args = ['/usr/bin/git', 'clone']
    if p['branch']:
        args += ['--branch', p['branch']]
    subprocess.run([*args, '--', p['repo'], str(workspace)], check=True,
        stdout=subprocess.DEVNULL, user=uid, group=uid, extra_groups=[], cwd=str(home),
        env={'PATH': '/usr/bin:/bin', 'HOME': str(home)})
# -h: a link the session left in its storage names its own path, never a target.
subprocess.run(['find', str(root), '-exec', 'chown', '-h', '%d:%d' % (uid, uid), '{}', '+'],
    check=True)
for item in p.get('directories', []):
    # Written by root and the Forge's agents, read by the session (its group); a volume the
    # session writes to is its group's too.
    path = pathlib.Path(item['path'])
    if path.is_symlink():
        raise RuntimeError(str(path) + ' is a link')
    path.mkdir(parents=True, exist_ok=True)
    os.chown(str(path), 0, uid)
    os.chmod(str(path), int(item['mode'], 8))
if d['nix'] and not os.path.exists('/nix/var/nix/daemon-socket/socket'):
    raise RuntimeError('This Mac has no Nix daemon; install Nix through the host recipe '
        'or set compute.runtime.kwargs.nix: false')
address = p.get('metadata_address')
if address:
    shown = subprocess.run(['ifconfig', 'lo0', 'inet'], check=True, capture_output=True,
        text=True).stdout
    if ' ' + address + ' ' not in shown:
        subprocess.run(['ifconfig', 'lo0', 'alias', address, 'netmask', '255.255.255.255'],
            check=True)
for item in d['links']:
    # Paths the session's tools read at fixed places outside its storage (a workload token
    # under /var/run/secrets): a link of root's to what the session would see mounted.
    link = pathlib.Path(item['target'])
    if link.is_symlink() and os.readlink(str(link)) == item['source']:
        continue
    if os.path.lexists(str(link)):
        raise RuntimeError(str(link) + ' exists on this Mac; the session expects its own there')
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(item['source'], str(link))
state = pathlib.Path(d['state_root'])
state.mkdir(parents=True, exist_ok=True)
os.chmod(str(state), 0o700)
logs = pathlib.Path(d['log_root'])
logs.mkdir(parents=True, exist_ok=True)
os.chmod(str(logs), 0o700)
def native(c):
    environment = {'PATH': d['native_path'], 'HOME': d['agent_home'], 'TMPDIR': d['agent_tmp'],
        **c['environment']}
    return c['command'] + c['args'], environment
pathlib.Path(d['agent_home']).mkdir(parents=True, exist_ok=True)
pathlib.Path(d['agent_tmp']).mkdir(parents=True, exist_ok=True)
for c in p.get('init_containers', []):
    command, environment = native(c)
    with open(str(logs / (c['name'] + '.log')), 'ab') as log:
        result = subprocess.run(command, env=environment, cwd='/', stdout=log, stderr=log)
    if result.returncode:
        raise RuntimeError('Init container ' + c['name'] + ' exited with status '
            + str(result.returncode) + '; see ' + str(logs / (c['name'] + '.log')) + ' on the Mac')
for c in p.get('sidecars', []):
    sidecar = label + '.' + c['name']
    if loaded(sidecar):
        continue
    command, environment = native(c)
    plist = '/Library/LaunchDaemons/' + sidecar + '.plist'
    write_plist(plist, {
        'Label': sidecar, 'ProgramArguments': command, 'EnvironmentVariables': environment,
        'WorkingDirectory': '/', 'RunAtLoad': True, 'KeepAlive': True,
        'ExitTimeOut': d['stop_timeout'],
        'StandardOutPath': str(logs / (c['name'] + '.log')),
        'StandardErrorPath': str(logs / (c['name'] + '.log')),
    })
    subprocess.run(['launchctl', 'bootstrap', 'system', plist], check=True)
spec = state / 'session.json'
fd = os.open(str(spec), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as out:
    json.dump({'uid': uid, 'workdir': str(workspace), 'boot': d['boot'],
        'environment': p['environment'], 'log': str(logs / 'skuld.log')}, out)
init = state / 'init.py'
init.write_text(d['init'])
os.chmod(str(init), 0o600)
plist = '/Library/LaunchDaemons/' + label + '.plist'
write_plist(plist, {
    'Label': label, 'ProgramArguments': [d['python'], '-I', str(init), str(spec)],
    'WorkingDirectory': '/', 'RunAtLoad': True, 'KeepAlive': False,
    'ExitTimeOut': d['stop_timeout'], 'ProcessType': 'Interactive',
})
subprocess.run(['launchctl', 'bootstrap', 'system', plist], check=True)
deadline = time.monotonic() + d['start_timeout']
while not running() and time.monotonic() < deadline:
    time.sleep(d['poll'])
if not running():
    raise RuntimeError('The session did not start; see ' + str(logs / 'skuld.log') + ' on the Mac')
"""
)

#: The session job's program, run by launchd as root: opens Skuld's log as root, then becomes
#: the slot's user and runs the boot script. argv: the spec root wrote for it.
INIT = r"""
import json, os, sys
spec = json.load(open(sys.argv[1]))
log = os.open(spec['log'], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
os.dup2(log, 1)
os.dup2(log, 2)
os.close(log)
uid = spec['uid']
os.setgroups([uid])
os.setgid(uid)
os.setuid(uid)
os.umask(0o022)
os.chdir(spec['workdir'])
os.execve('/bin/sh', ['/bin/sh', '-c', spec['boot']], spec['environment'])
"""

#: Stop the slot's session and its sidecars. argv: the stop spec (JSON). Exit 42: the
#: session was cancelled before its storage was prepared.
STOP = (
    _SLOT_COMMON
    + r"""
h = json.loads(sys.argv[1])
for label in h['labels']:
    # The session first, so Skuld can still flush through its agents.
    bootout(label, h['stop_timeout'], h['poll'])
    pathlib.Path('/Library/LaunchDaemons/' + label + '.plist').unlink(missing_ok=True)
pathlib.Path(h['state_root'], 'session.json').unlink(missing_ok=True)
marker = pathlib.Path(h['data_root']) / '.allocation'
if not marker.exists():
    sys.exit(42)
if marker.read_text() != h['marker']:
    raise RuntimeError('Workspace allocation mismatch')
"""
)

#: What the session's job runs as the slot's user: the secrets the delivery rendered become
#: environment and files, then Skuld runs from the bundle. On Linux the secrets are mounted
#: at their targets; here the manifest's targets in the session's home and workspace
#: become links into the rendered secrets.
BOOT = r"""
set -eu
if [ -n "${NIUU_SECRETS_DIR:-}" ] && [ -f "$NIUU_SECRETS_DIR/manifest.json" ] && \
    [ -d "$NIUU_SECRETS_DIR/user" ]; then
    # Assigned first: under set -e a failing substitution inside eval would go unnoticed.
    exports=$("$NIUU_PYTHON" -I -c '
import json, os, shlex
secrets = os.environ["NIUU_SECRETS_DIR"]
places = json.loads(os.environ["NIUU_SESSION_PLACES"])
def place(target):
    for container, here in sorted(places.items(), key=lambda item: -len(item[0])):
        if target == container or target.startswith(container + "/"):
            return here + target[len(container):]
    return None
manifest = json.load(open(os.path.join(secrets, "manifest.json")))
for name, spec in manifest.get("env", {}).items():
    path = os.path.join(secrets, "user", spec["file"])
    if os.path.exists(path):
        value = json.load(open(path)).get(spec.get("key", ""), "")
        print("export " + name + "=" + shlex.quote(value))
for target, spec in manifest.get("files", {}).items():
    source = os.path.join(secrets, "user", spec["file"])
    where = place(target)
    if where is None:
        raise SystemExit("niuu: secret file " + target + " lies outside the session home and "
            "workspace, where a macOS session has no mounts")
    if os.path.exists(source) and not os.path.lexists(where):
        os.makedirs(os.path.dirname(where), exist_ok=True)
        os.symlink(source, where)
')
    eval "$exports"
fi
if [ -n "${NIUU_SECRETS_DIR:-}" ] && [ -f "$NIUU_SECRETS_DIR/env.sh" ]; then
    . "$NIUU_SECRETS_DIR/env.sh"
fi
"$NIUU_PYTHON" -I -c '
import json, os
for item in json.loads(os.environ["NIUU_SESSION_LINKS"]):
    target = item["target"]
    if os.path.islink(target) and os.readlink(target) == item["source"]:
        continue
    if os.path.lexists(target):
        raise SystemExit("niuu: " + target + " is in the way of what the session mounts there")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    os.symlink(item["source"], target)
'
skuld=$NIUU_SKULD
unset NIUU_SESSION_LINKS NIUU_SESSION_PLACES NIUU_SECRETS_DIR NIUU_PYTHON NIUU_SKULD
export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$CLAUDE_CONFIG_DIR" "$WORKSPACE_DIR" "$TMPDIR"
exec "$skuld"
"""
