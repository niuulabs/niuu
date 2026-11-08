"""Guest programs of the Linux host runtime, which runs no container daemon.

A registered host is not disposable: a root daemon a session could reach (root Docker)
reads every other slot and the host itself. So what the VM runtimes do with the guest's
Docker, these programs do with the kernel, systemd and the standard library:

* ``IMAGE`` fetches a pod image from its registry (anonymous pull, every blob checked
  against its digest) and unpacks its layers once per digest, applying whiteouts, into
  ``/var/lib/niuu/host-images/<digest>/rootfs``. No container engine is involved.
* ``PRUNE`` removes the unpacked images nothing uses. Several slots start and run at
  once, so what is in use is decided under the cache's lock (``_CACHE``): a root a slot
  claimed (``IMAGE`` records it for the slot before it looks at the root), one any mount
  table names, one an unpack running now holds, and the ones the caller keeps.
* ``CONTAINER`` runs one pod container from such a root: in the slot's network
  namespace, its own mount and PID namespaces, on an overlay whose writable layer is a
  fresh tmpfs (as a container's is), with the pod's mounts bound at their targets. It is
  started as a systemd unit in the slot's slice, so the slot's limits hold it.
* ``PODS_START`` runs the pod's init containers to completion and starts its sidecars
  (each a ``niuu-slot-<n>-sidecar-<name>.service``); ``PODS_STOP`` stops them.

Each is shipped over the pinned SSH channel and run as root with ``python3 -c``, like
every other guest step. They import only the standard library.
"""

from __future__ import annotations

#: Where pod images are unpacked on a host, one directory per manifest digest.
IMAGE_CACHE = "/var/lib/niuu/host-images"

#: Where a slot's claims on unpacked images are kept: ``<slot>.json`` with the digests it
#: unpacked or reused and the paths that exist while the slot does (``alive``).
CLAIMS = ".claims"

#: Shared by ``IMAGE`` and ``PRUNE``: the cache's lock and the directory locks of a run.
#: Whatever an unpack claims or starts to hold, and whatever a prune decides to remove, is
#: done under the cache's lock (held for moments, never across a download or a removal).
#: An unpack holds a shared lock on each directory it uses (its work directories, the root
#: it reads) while it runs; a prune removes only what it can lock exclusively.
_CACHE = r"""
import fcntl, os, pathlib, tempfile
HELD = []
class locked:
    def __init__(self, cache):
        self.path = str(pathlib.Path(cache) / '.lock')
    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
    def __exit__(self, *exc):
        os.close(self.fd)
def hold(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_SH)
    HELD.append(fd)
def scratch(cache, prefix):
    # Made and held at once, under the cache's lock: a prune never sees it unheld.
    with locked(cache):
        path = pathlib.Path(tempfile.mkdtemp(dir=str(cache), prefix=prefix))
        hold(path)
    return path
"""

#: Fetch and unpack one image. argv: reference (``registry/repo[:tag][@sha256:…]``), cache
#: root, a JSON object: ``null``, or for the session image ``{"agents", "tools", "view"}``
#: (wrappers for the commands the session runs from it), and the claim: ``null`` (a warm
#: unpack, which no slot runs from) or ``{"slot", "alive"}``, the slot that will run from
#: the image and the paths that exist while it does. Prints the image's description
#: (``host.json``): digest, config (env, user, workdir, entrypoint, cmd) and, for the
#: session image, its relocatable Python.
IMAGE = (
    _CACHE
    + r"""
import atexit, hashlib, json, os, pathlib, platform, re, shlex, shutil, subprocess, sys
import tarfile
import tempfile
reference, cache, session = sys.argv[1], pathlib.Path(sys.argv[2]), json.loads(sys.argv[3])
claimant = json.loads(sys.argv[4])
if claimant is not None and (not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', claimant['slot'])
        or not claimant['alive'] or not all(path.startswith('/') for path in claimant['alive'])):
    raise RuntimeError('Not a slot claim: ' + sys.argv[4])
if not hasattr(tarfile, 'tar_filter'):
    raise RuntimeError('python3 on this host has no tarfile extraction filters; install a '
        'python3 with security updates (3.10.12, 3.11.4, 3.12 or later)')
ARCH = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}
arch = ARCH.get(platform.machine())
if arch is None:
    raise RuntimeError('No image platform for this host architecture ' + platform.machine())
name, _, pinned = reference.partition('@')
if pinned and not re.fullmatch(r'sha256:[a-f0-9]{64}', pinned):
    raise RuntimeError('Not an image digest: ' + reference)
head, _, last = name.rpartition('/')
tag = 'latest'
if ':' in last:
    last, tag = last.split(':', 1)
name = (head + '/' if head else '') + last
first = name.split('/', 1)[0]
if '/' not in name or ('.' not in first and ':' not in first and first != 'localhost'):
    registry, repository = 'registry-1.docker.io', name
    if '/' not in repository:
        repository = 'library/' + repository
elif first == 'docker.io':
    registry, repository = 'registry-1.docker.io', name.split('/', 1)[1]
else:
    registry, repository = name.split('/', 1)
cache.mkdir(parents=True, exist_ok=True)
os.chmod(str(cache), 0o755)
work = scratch(cache, '.fetch-')
# Whatever fails, nothing half-fetched stays (a finished unpack has moved it in place).
atexit.register(shutil.rmtree, str(work), True)
token = ['']
INDEX = ('application/vnd.oci.image.index.v1+json',
    'application/vnd.docker.distribution.manifest.list.v2+json')
MANIFEST = ('application/vnd.oci.image.manifest.v1+json',
    'application/vnd.docker.distribution.manifest.v2+json')
def curl(url, accept, output, bearer):
    headers = ['-H', 'Accept: ' + accept] if accept else []
    if bearer:
        headers += ['-H', 'Authorization: Bearer ' + bearer]
    return subprocess.run(['curl', '--silent', '--show-error', '--location', '--retry', '3',
        '--output', str(output), '--write-out', '%{http_code}', '--dump-header',
        str(output) + '.headers', *headers, url], check=True, capture_output=True,
        text=True).stdout.strip()
def challenge(headers):
    # The registry's Bearer challenge names where an anonymous pull token comes from.
    value = ''
    for line in pathlib.Path(headers).read_text().splitlines():
        if line.lower().startswith('www-authenticate:'):
            value = line.split(':', 1)[1].strip()
    if not value.lower().startswith('bearer '):
        raise RuntimeError('The registry ' + registry + ' asks for no bearer token: ' + value)
    fields = dict(re.findall(r'(\w+)="([^"]*)"', value))
    query = '&'.join(k + '=' + fields[k] for k in ('service', 'scope') if k in fields)
    out = work / 'token.json'
    status = curl(fields['realm'] + ('?' + query if query else ''), '', out, '')
    if status != '200':
        raise RuntimeError('The registry ' + registry + ' refused an anonymous pull token '
            '(HTTP ' + status + '); pod images on registered hosts must be public')
    answer = json.loads(out.read_text())
    return answer.get('token') or answer['access_token']
def get(path, accept, out):
    url = 'https://' + registry + '/v2/' + repository + '/' + path
    status = curl(url, accept, out, token[0])
    if status == '401':
        token[0] = challenge(str(out) + '.headers')
        status = curl(url, accept, out, token[0])
    if status != '200':
        raise RuntimeError('Fetching ' + url + ' answered HTTP ' + status)
    return out
def sha256(path):
    digest = hashlib.sha256()
    with open(str(path), 'rb') as data:
        for chunk in iter(lambda: data.read(1 << 20), b''):
            digest.update(chunk)
    return 'sha256:' + digest.hexdigest()
def blob(digest):
    out = get('blobs/' + digest, '', work / digest.replace(':', '-'))
    if sha256(out) != digest:
        raise RuntimeError('The registry served blob ' + digest + ' with digest ' + sha256(out))
    return out
def manifest(selector, expected):
    out = get('manifests/' + selector, ','.join(INDEX + MANIFEST), work / 'manifest.json')
    digest = sha256(out)
    if expected and digest != expected:
        raise RuntimeError('The registry served ' + reference + ' with digest ' + digest)
    return json.loads(out.read_text()), digest
document, digest = manifest(pinned or tag, pinned)
if document.get('mediaType') in INDEX or 'manifests' in document:
    found = [m for m in document['manifests'] if m.get('platform', {}).get('os') == 'linux'
        and m.get('platform', {}).get('architecture') == arch]
    if not found:
        shutil.rmtree(str(work), ignore_errors=True)
        raise RuntimeError(reference + ' has no linux/' + arch + ' image')
    document, digest = manifest(found[0]['digest'], found[0]['digest'])
root = cache / digest.split(':', 1)[1]
def claim(name):
    # The slot's claim on the root, recorded before the root is looked at: from here on no
    # prune removes it while the slot is there, whoever unpacks it.
    claims = cache / '"""
    + CLAIMS
    + r"""'
    claims.mkdir(mode=0o700, exist_ok=True)
    path = claims / (claimant['slot'] + '.json')
    record = json.loads(path.read_text()) if path.exists() else {'digests': []}
    record['alive'] = claimant['alive']
    if name not in record['digests']:
        record['digests'].append(name)
    temporary = claims / ('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(record))
    os.replace(str(temporary), str(path))
with locked(cache):
    if claimant is not None:
        claim(root.name)
    present = (root / 'host.json').exists()
    if present:
        hold(root)
def add_session_extras(description):
    # What a session needs of its image (its relocatable Python, wrappers for the commands it
    # runs from it), added to the image's cached root once, whoever unpacked it first.
    rootfs = root / 'rootfs'
    extras = scratch(cache, '.session-')
    atexit.register(shutil.rmtree, str(extras), True)
    env = dict(item.split('=', 1) for item in description['env'])
    venv, python_root = env.get('VIRTUAL_ENV'), env.get('UV_PYTHON_INSTALL_DIR')
    if not venv or not python_root:
        raise RuntimeError('The session image declares no VIRTUAL_ENV and '
            'UV_PYTHON_INSTALL_DIR; host mode needs an image on a relocatable Python, '
            'such as ghcr.io/niuulabs/openshell')
    def resolve(path):
        # A path inside the image, following its links as the image itself would.
        parts, done, seen = [p for p in pathlib.PurePosixPath(path).parts if p != '/'], [], 0
        while parts:
            part = parts.pop(0)
            if part == '..':
                done = done[:-1]
                continue
            candidate = rootfs.joinpath(*done, part)
            if candidate.is_symlink():
                seen += 1
                if seen > 64:
                    raise RuntimeError('Symbolic link loop in the session image at ' + path)
                target = pathlib.PurePosixPath(os.readlink(str(candidate)))
                if target.is_absolute():
                    done = []
                parts = [p for p in target.parts if p != '/'] + parts
                continue
            done.append(part)
        return '/' + '/'.join(done)
    python = resolve(venv + '/bin/python')
    if not python.startswith(python_root.rstrip('/') + '/') or not (
            rootfs / python[1:]).is_file():
        raise RuntimeError('The session image venv ' + venv + ' does not run on its own '
            'Python under ' + python_root + '; host mode needs a relocatable Python')
    search = env.get('PATH', '/usr/local/bin:/usr/bin:/bin').split(':')
    def found(command):
        for directory in search:
            # lexists: an absolute link names a path in the image, not on the host.
            if os.path.lexists(str(rootfs / directory.lstrip('/') / command)):
                target = resolve(directory + '/' + command)
                if (rootfs / target[1:]).is_file():
                    return target
        return None
    def wrapper(directory, command_name):
        target = found(command_name)
        if target is None:
            return
        command = [session['view'] + '/rootfs' + target]
        with (rootfs / target[1:]).open('rb') as program:
            line = program.readline(256)
        if line.startswith(b'#!/usr/bin/env '):
            # The image's own interpreter, not whichever one the host has on PATH.
            interpreter = found(line.split()[1].decode())
            if interpreter is None:
                raise RuntimeError('The session image has no interpreter for ' + command_name)
            command.insert(0, session['view'] + '/rootfs' + interpreter)
        path = extras / directory / command_name
        path.write_text('#!/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n')
        path.chmod(0o755)
    for directory, names in (('agents', session['agents']), ('tools', session['tools'])):
        (extras / directory).mkdir()
        for command_name in names:
            wrapper(directory, command_name)
    description.update({'venv': venv, 'python_root': python_root})
    for directory in ('agents', 'tools'):
        (extras / directory).chmod(0o755)
        try:
            os.rename(str(extras / directory), str(root / directory))
        except OSError:
            # A concurrent run added them first.
            if not (root / directory).is_dir():
                raise
    temporary = root / '.host.json.tmp'
    temporary.write_text(json.dumps(description))
    os.replace(str(temporary), str(root / 'host.json'))
    return description
if not present:
    def within(path):
        base = os.path.realpath(str(rootfs))
        resolved = os.path.realpath(str(path))
        return resolved == base or resolved.startswith(base + os.sep)
    def safe(path):
        # An entry is removed by its own name, never followed: only its directory must lie in
        # the root (a link may point anywhere; it is unlinked, not its target).
        if not within(path.parent):
            raise RuntimeError('Image layer entry outside the root: ' + str(path))
        return path
    def remove(path):
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(str(path))
    try:
        config = json.loads(blob(document['config']['digest']).read_text()).get('config') or {}
        rootfs = work / 'rootfs'
        rootfs.mkdir()
        for layer in document['layers']:
            media = layer.get('mediaType', '')
            if 'zstd' in media:
                raise RuntimeError(reference + ' has zstd layers, which this host cannot unpack; '
                    'publish the image with gzip layers')
            archive = blob(layer['digest'])
            with tarfile.open(str(archive), 'r:*') as layer_tar:
                for member in layer_tar:
                    parts = pathlib.PurePosixPath(member.name.lstrip('/')).parts
                    if not parts or parts[0] in ('dev', 'proc', 'sys'):
                        continue
                    base = parts[-1]
                    parent = rootfs.joinpath(*parts[:-1])
                    if base == '.wh..wh..opq':
                        if parent.is_dir() and not parent.is_symlink():
                            for entry in list(parent.iterdir()):
                                remove(safe(entry))
                        continue
                    if base.startswith('.wh.'):
                        target = parent / base[len('.wh.'):]
                        if os.path.lexists(str(target)):
                            remove(safe(target))
                        continue
                    if member.isdev() or member.isfifo():
                        continue
                    # A hard link names a member of the archive. tarfile joins the name to the
                    # root, and an absolute one replaces it: it would link a file of this host.
                    if member.islnk() and not within(rootfs / member.linkname):
                        raise RuntimeError('Image layer hard link outside the root: ' + member.name)
                    target = rootfs.joinpath(*parts)
                    if os.path.lexists(str(target)) and not (member.isdir() and target.is_dir()
                            and not target.is_symlink()):
                        remove(safe(target))
                    layer_tar.extract(member, str(rootfs), filter='tar')
            os.unlink(str(archive))
        description = {
            'digest': digest,
            'reference': reference,
            'env': config.get('Env') or [],
            'user': config.get('User') or '',
            'workdir': config.get('WorkingDir') or '',
            'entrypoint': config.get('Entrypoint') or [],
            'cmd': config.get('Cmd') or [],
        }
        for leftover in work.iterdir():
            if leftover.name != 'rootfs':
                remove(leftover)
        (work / 'host.json').write_text(json.dumps(description))
        work.chmod(0o755)
        try:
            # The lock this run holds on its work directory now holds the root.
            os.rename(str(work), str(root))
        except OSError:
            # A concurrent unpack of the same digest finished first.
            if not (root / 'host.json').exists():
                raise
            with locked(cache):
                hold(root)
    except BaseException:
        shutil.rmtree(str(work), ignore_errors=True)
        raise
shutil.rmtree(str(work), ignore_errors=True)
description = json.loads((root / 'host.json').read_text())
if session and 'venv' not in description:
    description = add_session_extras(description)
print(json.dumps(description))
"""
)

#: Remove the unpacked images nothing uses. argv: cache root, the references to keep (JSON
#: list: the release's session image and a starting launch's images, matched by their
#: pinned digest or the reference they were unpacked by) and the proc root (``/proc``).
#: A root stays while a live slot claims it (a claim whose ``alive`` paths are all gone is
#: dropped: the slot is torn down and nothing of it runs), while any mount table names it
#: (a session started before claims existed), while an unpack holds it, or when it is
#: kept. Unpack and prune leftovers (``.fetch-``, ``.session-``, ``.trash-``) go unless a
#: running program holds them. Prints what it removed, what is in use or kept, and entries
#: of the cache Forge did not make (left alone).
PRUNE = (
    _CACHE
    + r"""
import json, re, shutil, sys
cache, keep, proc = pathlib.Path(sys.argv[1]), json.loads(sys.argv[2]), pathlib.Path(sys.argv[3])
ROOT = re.compile(r'[a-f0-9]{64}')
LEFTOVER = ('.fetch-', '.session-', '.trash-')
pinned = {reference.partition('@sha256:')[2] for reference in keep} - {''}
result = {'removed': [], 'in_use': [], 'kept': [], 'unknown': []}
if not cache.is_dir():
    print(json.dumps(result))
    sys.exit(0)
def mounted(names):
    found, seen = set(), set()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            namespace = os.readlink(str(entry / 'ns' / 'mnt'))
            if namespace in seen:
                continue
            table = (entry / 'mountinfo').read_text()
        except (FileNotFoundError, ProcessLookupError):
            # The process ended: its mounts went with its namespace, or another of its
            # processes shows them.
            continue
        seen.add(namespace)
        found.update(set(ROOT.findall(table)) & names)
    return found
def unpacked_by(root):
    try:
        return json.loads((root / 'host.json').read_text()).get('reference')
    except FileNotFoundError:
        return None
def unheld(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd
with locked(cache):
    claimed = set()
    claims = cache / '"""
    + CLAIMS
    + r"""'
    for record_path in sorted(claims.glob('*.json')) if claims.is_dir() else ():
        record = json.loads(record_path.read_text())
        if any(os.path.lexists(path) for path in record['alive']):
            claimed.update(record['digests'])
        else:
            record_path.unlink()
    entries = sorted(cache.iterdir())
    roots = {e.name for e in entries if ROOT.fullmatch(e.name) and e.is_dir()
        and not e.is_symlink()}
    named = mounted(roots)
    # Under the lock already: made and held here, not by scratch.
    trash = pathlib.Path(tempfile.mkdtemp(dir=str(cache), prefix='.trash-'))
    hold(trash)
    for entry in entries:
        name = entry.name
        if name in ('.lock', '"""
    + CLAIMS
    + r"""'):
            continue
        if name in roots:
            if name in claimed or name in named:
                result['in_use'].append(name)
                continue
            if name in pinned or unpacked_by(entry) in keep:
                result['kept'].append(name)
                continue
        elif not (name.startswith(LEFTOVER) and entry.is_dir() and not entry.is_symlink()):
            result['unknown'].append(name)
            continue
        fd = unheld(entry)
        if fd is None:
            # An unpack (or another prune) running now holds it.
            result['in_use'].append(name)
            continue
        os.rename(str(entry), str(trash / name))
        os.close(fd)
        if name in roots:
            result['removed'].append(name)
# Removed outside the lock: nothing can claim what is in the trash.
shutil.rmtree(str(trash))
print(json.dumps(result))
"""
)

#: Run one pod container from its unpacked root. argv: the container's spec (a root-only
#: JSON file written by ``PODS_START``). PID 1 of its own PID namespace reaps for it and
#: passes stop signals on; the container's exit status is this program's.
CONTAINER = r"""
import json, os, pathlib, signal, subprocess, sys
spec = json.load(open(sys.argv[1]))
def mount(*args):
    subprocess.run(['mount', *args], check=True)
fd = os.open('/run/netns/' + spec['netns'], os.O_RDONLY)
os.setns(fd, os.CLONE_NEWNET)
os.close(fd)
os.unshare(os.CLONE_NEWNS)
mount('--make-rprivate', '/')
work = pathlib.Path(spec['work'])
work.mkdir(parents=True, exist_ok=True, mode=0o700)
# The writable layer: fresh on every start, as a container's is, and gone with its namespace.
mount('-t', 'tmpfs', '-o', 'mode=0700', 'tmpfs', str(work))
for name in ('upper', 'work', 'root'):
    (work / name).mkdir()
root = work / 'root'
mount('-t', 'overlay', 'overlay', '-o', 'lowerdir=' + spec['rootfs'] + ',upperdir='
    + str(work / 'upper') + ',workdir=' + str(work / 'work'), str(root))
def inside(target):
    return root / target.lstrip('/')
def mountpoint(target, directory):
    path = inside(target)
    if os.path.islink(str(path)):
        raise RuntimeError('Mount target ' + target + ' is a link in the image')
    if directory:
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
    return str(path)
for target, directory in (('/proc', True), ('/dev', True), ('/sys', True), ('/tmp', True),
        ('/etc/resolv.conf', False), ('/etc/hosts', False)):
    mountpoint(target, directory)
mount('-t', 'tmpfs', '-o', 'mode=0755,nosuid', 'tmpfs', str(inside('/dev')))
for device in ('null', 'zero', 'full', 'random', 'urandom', 'tty'):
    mount('--bind', '/dev/' + device, mountpoint('/dev/' + device, False))
mount('-t', 'tmpfs', '-o', 'mode=1777,nosuid,nodev', 'tmpfs', mountpoint('/dev/shm', True))
mount('-t', 'tmpfs', '-o', 'mode=1777,nosuid,nodev', 'tmpfs', str(inside('/tmp')))
mount('-o', 'ro', '--bind', spec['resolv_conf'], str(inside('/etc/resolv.conf')))
(work / 'hosts').write_text('127.0.0.1 localhost\n::1 localhost\n')
mount('-o', 'ro', '--bind', str(work / 'hosts'), str(inside('/etc/hosts')))
for item in spec['mounts']:
    target = mountpoint(item['target'], os.path.isdir(item['source']))
    mount('--bind', item['source'], target)
    if item['readonly']:
        mount('-o', 'remount,bind,ro', target)
os.unshare(os.CLONE_NEWPID)
child = os.fork()
if child:
    def forward(signum, frame):
        os.kill(child, signum)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, forward)
    while True:
        try:
            _, status = os.waitpid(child, 0)
            break
        except InterruptedError:
            continue
    os._exit(os.waitstatus_to_exitcode(status) & 0xFF)
mount('-t', 'proc', 'proc', str(inside('/proc')))
mount('-t', 'sysfs', '-o', 'ro,nosuid,nodev,noexec', 'sysfs', str(inside('/sys')))
program = os.fork()
if not program:
    os.chroot(str(root))
    os.chdir(spec['workdir'] or '/')
    os.setgroups([spec['gid']])
    os.setgid(spec['gid'])
    os.setuid(spec['uid'])
    os.execvpe(spec['argv'][0], spec['argv'], spec['environment'])
def forward(signum, frame):
    os.kill(program, signum)
for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(signum, forward)
while True:
    try:
        pid, status = os.waitpid(-1, 0)
    except InterruptedError:
        continue
    if pid == program:
        os._exit(os.waitstatus_to_exitcode(status) & 0xFF)
"""

#: Shared by the pod programs: a slot's units and the commands that manage them.
_UNITS = r"""
import json, os, pathlib, subprocess, sys, time
def units(prefix):
    listed = subprocess.run(['systemctl', 'list-units', '--all', '--plain', '--no-legend',
        '--type=service', prefix + '*'], check=True, capture_output=True, text=True).stdout
    return [line.split()[0] for line in listed.splitlines() if line.strip()]
def active(unit):
    return subprocess.run(['systemctl', 'is-active', '--quiet', unit]).returncode == 0
def forget(unit):
    # A failed or finished unit of an earlier start keeps its name until reset.
    subprocess.run(['systemctl', 'reset-failed', unit], capture_output=True)
"""

#: Start the pod's containers in a slot. Stdin: the placed launch payload with its ``pods``
#: section (unit prefix, slice, netns, log and state directories, the CONTAINER program,
#: and each container's resolved image root and config). Init containers run to
#: completion first; sidecars are started unless they already run.
PODS_START = (
    _UNITS
    + r"""
p = json.load(sys.stdin)
s, d = p['slot'], p['pods']
for directory in p.get('directories', []):
    path = pathlib.Path(directory['path'])
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(int(directory['mode'], 8))
address = p.get('metadata_address')
if address:
    # The link-local address the guest agent serves credentials on, in the slot's namespace.
    ip = ['ip', 'netns', 'exec', s['netns'], 'ip']
    shown = subprocess.run([*ip, '-o', 'addr', 'show', 'dev', 'lo'], check=True,
        capture_output=True, text=True).stdout
    if ' ' + address + '/' not in shown:
        subprocess.run([*ip, 'addr', 'add', address + '/32', 'dev', 'lo'], check=True)
state, logs = pathlib.Path(d['state']), pathlib.Path(d['logs'])
for directory in (state, logs):
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(str(directory), 0o700)
program = state / 'container.py'
program.write_text(d['program'])
os.chmod(str(program), 0o600)
def users(rootfs, user):
    # Kubernetes semantics: the container's user, else the image's, else root. A name is
    # the image's own account, never the host's.
    name, _, group = (user or '0').partition(':')
    def lookup(file, wanted):
        try:
            for line in pathlib.Path(rootfs, 'etc', file).read_text().splitlines():
                fields = line.split(':')
                if fields and fields[0] == wanted:
                    return fields
        except FileNotFoundError:
            pass
        raise RuntimeError('The image has no ' + file + ' entry ' + wanted)
    if name.isdigit():
        uid, gid = int(name), None
    else:
        account = lookup('passwd', name)
        uid, gid = int(account[2]), int(account[3])
    if group:
        gid = int(group) if group.isdigit() else int(lookup('group', group)[2])
    if gid is None:
        gid = uid
    return uid, gid
def spec_for(c, kind):
    image = d['images'][c['image']]
    environment = dict(item.split('=', 1) for item in image['env'])
    environment.setdefault('PATH', '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
    environment.update(c['environment'])
    if c['command']:
        argv = c['command'] + c['args']
    else:
        argv = image['entrypoint'] + (c['args'] if c['args'] else image['cmd'])
    if not argv:
        raise RuntimeError('Container ' + c['name'] + ' names no command and its image none')
    uid, gid = users(image['rootfs'], c.get('user') or image['user'])
    environment.setdefault('HOME', '/root' if uid == 0 else '/')
    unit = d['prefix'] + kind + '-' + c['name']
    spec = {
        'netns': s['netns'],
        'rootfs': image['rootfs'],
        'work': d['work'] + '/' + kind + '-' + c['name'],
        'resolv_conf': d['resolv_conf'],
        'mounts': [{'source': m['source'], 'target': m['target'],
            'readonly': m.get('readonly', True)} for m in c['mounts']],
        'workdir': image['workdir'],
        'argv': argv,
        'environment': environment,
        'uid': uid,
        'gid': gid,
    }
    path = state / (kind + '-' + c['name'] + '.json')
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(spec, out)
    log = str(logs / (kind + '-' + c['name'] + '.log'))
    return unit, [
        'systemd-run', '--quiet', '--unit', unit, '--slice', d['slice'],
        '--property', 'KillMode=mixed',
        '--property', 'TimeoutStopSec=' + str(d['stop_timeout']),
        '--property', 'StandardOutput=append:' + log,
        '--property', 'StandardError=append:' + log,
    ], [sys.executable, '-I', str(program), str(path)], log
for c in p.get('init_containers', []):
    unit, run, command, log = spec_for(c, 'init')
    forget(unit)
    result = subprocess.run([*run, '--wait', '--collect', *command], capture_output=True,
        text=True)
    if result.returncode:
        raise RuntimeError('Init container ' + c['name'] + ' exited with status '
            + str(result.returncode) + '; see ' + log + ' on the host')
for c in p.get('sidecars', []):
    unit, run, command, log = spec_for(c, 'sidecar')
    if active(unit):
        continue
    forget(unit)
    subprocess.run([*run, '--property', 'Restart=always', '--property', 'RestartSec=1',
        *command], check=True, capture_output=True)
    deadline = time.monotonic() + d['start_timeout']
    while not active(unit) and time.monotonic() < deadline:
        time.sleep(d['poll'])
    if not active(unit):
        raise RuntimeError('Sidecar ' + c['name'] + ' did not start; see ' + log + ' on the host')
"""
)

#: Stop a slot's pod containers. argv: the stop spec (JSON): unit prefix, the storage
#: marker. Exit 42: the session was cancelled before its storage was prepared.
PODS_STOP = (
    _UNITS
    + r"""
h = json.loads(sys.argv[1])
marker = pathlib.Path(h['data_root']) / '.allocation'
if not marker.exists():
    for unit in units(h['prefix']):
        subprocess.run(['systemctl', 'stop', unit], check=True)
        forget(unit)
    sys.exit(42)
if marker.read_text() != h['marker']:
    raise RuntimeError('Workspace allocation mismatch')
for unit in units(h['prefix']):
    # SIGTERM, then SIGKILL after the unit's TimeoutStopSec (the runtime's stop timeout).
    subprocess.run(['systemctl', 'stop', unit], check=True)
    forget(unit)
"""
)
