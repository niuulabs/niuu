"""Run Skuld in an OpenShell sandbox hosted on an SSH-controlled VM."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any

import yaml

from niuu.ports.credentials import CredentialStorePort
from volundr.adapters.outbound.ssh_vm_runtime import (
    _CODEX_TMP_ARCHIVE_PATH,
    _LAUNCH,
    _PREPARE,
    _REMOTE_DATA,
    _SESSION_FILES,
    SshContainerVmRuntime,
)
from volundr.domain.compute import ComputeLease, MachineBootstrap
from volundr.domain.models import Session, SessionSpec

_OPEN_SHELL_ERROR_PREFIX = "NIIU_OPEN_SHELL_STAGE:"
_OPEN_SHELL_DETAIL_PREFIX = "NIIU_OPEN_SHELL_DETAIL:"
_OPEN_SHELL_HOME = "/sandbox/home"
_OPEN_SHELL_WORKSPACE = "/sandbox/workspace"
_OPEN_SHELL_CODEX_HOME = f"{_OPEN_SHELL_HOME}/.codex"
_OPEN_SHELL_CLAUDE_HOME = f"{_OPEN_SHELL_HOME}/.claude"


def _credential_mappings(values: dict[str, Any]) -> list[dict[str, Any]]:
    openshell = values.get("openshell")
    if not isinstance(openshell, dict):
        return []
    mappings = openshell.get("credentialMappings") or openshell.get("credential_mappings")
    if not isinstance(mappings, list):
        return []
    return [dict(item) for item in mappings if isinstance(item, dict)]


def _native_provider_target(env_name: str, configured: object) -> dict[str, Any]:
    if isinstance(configured, dict):
        endpoints = configured.get("endpoints")
        binaries = configured.get("binaries")
        if not isinstance(endpoints, list) or not endpoints:
            raise ValueError("OpenShell credential provider requires endpoints")
        if not isinstance(binaries, list) or not binaries:
            raise ValueError("OpenShell credential provider requires binaries")
        return {
            "auth_style": str(
                configured.get("authStyle") or configured.get("auth_style") or "bearer"
            ),
            "header_name": str(
                configured.get("headerName") or configured.get("header_name") or "Authorization"
            ),
            "endpoints": [dict(item) for item in endpoints if isinstance(item, dict)],
            "binaries": [
                str(item.get("path") if isinstance(item, dict) else item) for item in binaries
            ],
        }
    known = {
        "CLAUDE_CODE_OAUTH_TOKEN": (
            "bearer",
            "Authorization",
            ("api.anthropic.com",),
            ("/opt/niuu/**", "/usr/bin/node", "/usr/local/bin/node", "/usr/local/bin/claude"),
        ),
        "ANTHROPIC_API_KEY": (
            "header",
            "x-api-key",
            ("api.anthropic.com",),
            ("/opt/niuu/**", "/usr/bin/node", "/usr/local/bin/node", "/usr/local/bin/claude"),
        ),
        "CLAUDE_API_KEY": (
            "header",
            "x-api-key",
            ("api.anthropic.com",),
            ("/opt/niuu/**", "/usr/bin/node", "/usr/local/bin/node", "/usr/local/bin/claude"),
        ),
        "OPENAI_API_KEY": (
            "bearer",
            "Authorization",
            ("api.openai.com",),
            ("/opt/niuu/**", "/usr/bin/node", "/usr/local/bin/node", "/usr/local/bin/codex"),
        ),
        "GITLAB_TOKEN": (
            "bearer",
            "Authorization",
            ("gitlab.com",),
            (
                "/opt/niuu/**",
                "/usr/bin/glab",
                "/usr/bin/git",
                "/usr/lib/git-core/git-remote-http",
                "/usr/lib/git-core/git-remote-https",
                "/usr/bin/curl",
            ),
        ),
    }
    if env_name not in known:
        raise ValueError(f"OpenShell has no native provider target for {env_name!r}")
    auth_style, header_name, hosts, binaries = known[env_name]
    return {
        "auth_style": auth_style,
        "header_name": header_name,
        "endpoints": [
            {
                "host": host,
                "port": 443,
                "protocol": "rest",
                "tls": "terminate",
                "enforcement": "enforce",
                "access": "full",
            }
            for host in hosts
        ],
        "binaries": list(binaries),
    }


def _native_provider_specs(
    session_id: str,
    values: dict[str, Any],
    *,
    reject_files: bool = True,
    resolve_targets: bool = True,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mapping in _credential_mappings(values):
        credential_name = str(
            mapping.get("credentialName") or mapping.get("credential_name") or ""
        ).strip()
        env_mappings = mapping.get("envMappings") or mapping.get("env_mappings") or {}
        file_mappings = mapping.get("fileMappings") or mapping.get("file_mappings") or {}
        if file_mappings:
            if reject_files:
                raise ValueError("SSH OpenShell credentials must use native providers, not files")
            continue
        if not credential_name or not isinstance(env_mappings, dict):
            continue
        for env_name, field_name in env_mappings.items():
            env_name, field_name = str(env_name), str(field_name)
            if not env_name or not field_name:
                raise ValueError("OpenShell credential mappings must name an environment and field")
            digest = hashlib.sha256(
                f"{session_id}:{credential_name}:{field_name}:{env_name}".encode()
            ).hexdigest()[:14]
            provider_name = f"niuu-{digest}"
            if provider_name in seen:
                continue
            seen.add(provider_name)
            if not resolve_targets:
                specs.append({"name": provider_name})
                continue
            target = _native_provider_target(env_name, mapping.get("provider"))
            profile = {
                "id": provider_name,
                "display_name": f"Niuu {env_name}",
                "description": "Niuu session credential mediated by OpenShell",
                "category": "agent",
                "credentials": [
                    {
                        "name": env_name,
                        "description": f"Niuu-managed {env_name}",
                        "env_vars": [env_name],
                        "required": True,
                        "auth_style": target["auth_style"],
                        "header_name": target["header_name"],
                    }
                ],
                "endpoints": target["endpoints"],
                "binaries": target["binaries"],
                "inference_capable": True,
            }
            specs.append(
                {
                    "name": provider_name,
                    "credential_name": credential_name,
                    "credential_field": field_name,
                    "credential_env": env_name,
                    "profile": yaml.safe_dump(profile, sort_keys=False),
                    "network_policy": {
                        "name": provider_name,
                        "endpoints": target["endpoints"],
                        "binaries": [{"path": path} for path in target["binaries"]],
                    },
                }
            )
    return specs


def _policy_with_native_providers(configured_policy: str, specs: list[dict[str, Any]]) -> str:
    policy = yaml.safe_load(configured_policy)
    if not isinstance(policy, dict):
        raise ValueError("OpenShell policy must be a YAML object")
    network_policies = policy.setdefault("network_policies", {})
    if not isinstance(network_policies, dict):
        raise ValueError("OpenShell network_policies must be a YAML object")
    for spec in specs:
        network_policy = spec.get("network_policy")
        if not isinstance(network_policy, dict):
            raise ValueError("OpenShell native provider has no network policy")
        network_policies[spec["name"]] = network_policy
    return yaml.safe_dump(policy, sort_keys=False)


_INSTALL_OPENSHELL = r"""
import base64, os, pathlib, re, shutil, subprocess, sys, time

stage='host-install'
def report_stage(exc_type,exc_value,traceback):
    print('NIIU_OPEN_SHELL_STAGE:'+stage,file=sys.stderr)
    diagnostic=f'{exc_type.__name__}: {exc_value}'
    diagnostic=re.sub(r'[A-Za-z0-9_+/=-]{32,}','<redacted>',diagnostic)
    diagnostic=' '.join(diagnostic.split())[:800]
    diagnostic=''.join(char if 32 <= ord(char) <= 126 else '?' for char in diagnostic)
    if diagnostic:
        encoded=base64.b64encode(diagnostic.encode()).decode()
        print('NIIU_OPEN_SHELL_DETAIL:'+encoded,file=sys.stderr)
    sys.__excepthook__(exc_type,exc_value,traceback)
sys.excepthook=report_stage

def run(*args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.DEVNULL, **kwargs)

user=os.environ['USER']
run('sudo','-n','loginctl','enable-linger',user)
# Rootless Podman resolves host.containers.internal to its host-side bridge
# address, not guest loopback.  Permit the controller's explicitly requested
# remote-forward address so an OpenShell sandbox can call back to Volundr.
stage='host-ssh'
gateway_ports=pathlib.Path('/etc/ssh/sshd_config.d/20-niuu-gateway-ports.conf')
gateway_ports_text='GatewayPorts clientspecified\n'
if not gateway_ports.exists() or gateway_ports.read_text()!=gateway_ports_text:
    temporary=pathlib.Path('/tmp/niuu-openshell-gateway-ports.conf')
    temporary.write_text(gateway_ports_text)
    run('sudo','-n','install','-D','-m','0644',str(temporary),str(gateway_ports))
    temporary.unlink(missing_ok=True)
    run('sudo','-n','sshd','-t')
    run('sudo','-n','systemctl','restart','ssh')
if shutil.which('openshell') is None:
    installer=pathlib.Path('/tmp/niuu-openshell-install.sh')
    run('curl','-LsSf','https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh',
        '-o',str(installer))
    # The package installer starts the gateway immediately using its auto-detected
    # driver.  On an unconfigured VM that service start can fail before Niuu has
    # written the requested driver configuration below, even though installation
    # itself completed successfully.  Validate the installed artifacts instead;
    # the configured gateway readiness check remains the final success condition.
    subprocess.run(['sh',str(installer)],check=False,stdout=subprocess.DEVNULL)
    missing=[name for name in ('openshell','openshell-gateway') if shutil.which(name) is None]
    if missing:
        raise RuntimeError('OpenShell installer did not install: '+', '.join(missing))
# OpenShell gives its Podman sandbox explicit CPU limits.  Some guest images
# restrict user@.service to the memory/pids controllers, which makes rootless
# crun reject that sandbox even though ordinary Podman containers work.  This
# is a dedicated session VM, so delegate its controllers to this user manager.
stage='host-cgroup'
uid=os.getuid()
delegate=pathlib.Path(f'/etc/systemd/system/user@{uid}.service.d/90-niuu-openshell.conf')
delegate_text='[Service]\nDelegate=yes\n'
if not delegate.exists() or delegate.read_text()!=delegate_text:
    temporary=pathlib.Path('/tmp/niuu-openshell-user-delegate.conf')
    temporary.write_text(delegate_text)
    run('sudo','-n','install','-D','-m','0644',str(temporary),str(delegate))
    temporary.unlink(missing_ok=True)
    run('sudo','-n','systemctl','daemon-reload')
def delegated_controllers():
    manager_group=subprocess.check_output(
        ['systemctl','show',f'user@{uid}.service','-p','ControlGroup','--value'],text=True
    ).strip()
    return (pathlib.Path('/sys/fs/cgroup')/manager_group.lstrip('/')/
            'cgroup.controllers').read_text().split()
controllers=delegated_controllers()
if 'cpu' not in controllers:
    # Reload alone does not alter an already-running manager's delegation.
    # The SSH session is in its own logind scope, so restarting user@ keeps
    # this setup process alive while recreating the manager with the drop-in.
    run('sudo','-n','systemctl','restart',f'user@{uid}.service')
    for _ in range(10):
        time.sleep(1)
        controllers=delegated_controllers()
        if 'cpu' in controllers:
            break
if 'cpu' not in controllers:
    raise RuntimeError('OpenShell requires delegated CPU cgroup control')
stage='gateway-config'
config=pathlib.Path.home()/'.config/openshell/gateway.toml'
config.parent.mkdir(parents=True,exist_ok=True)
desired=f'''[openshell]
version = 1

[openshell.gateway]
bind_address = "0.0.0.0:17670"
compute_drivers = ["podman"]

[openshell.drivers.podman]
socket_path = "/run/user/{os.getuid()}/podman/podman.sock"
grpc_endpoint = "https://host.containers.internal:17670"
enable_bind_mounts = true
'''
if not config.exists() or config.read_text()!=desired:
    temporary=config.with_name(config.name+'.niuu-tmp')
    temporary.write_text(desired)
    os.chmod(temporary,0o600)
    os.replace(temporary,config)
tls_dir=pathlib.Path.home()/'.local/state/openshell/tls'
server_san='host.containers.internal'
san_marker=tls_dir/'.niuu-server-san'
if not san_marker.exists() or san_marker.read_text()!=server_san:
    subprocess.run(['systemctl','--user','stop','openshell-gateway'],check=False,
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    shutil.rmtree(tls_dir,ignore_errors=True)
    run('openshell-gateway','generate-certs','--output-dir',str(tls_dir),
        '--server-san',server_san)
    san_marker.write_text(server_san)
    os.chmod(san_marker,0o600)
version=subprocess.check_output(['podman','version','--format','{{.Client.Version}}'],text=True).strip()
if int(version.split('.',1)[0]) < 5:
    raise RuntimeError('OpenShell requires Podman 5 or newer')
run('systemctl','--user','enable','--now','podman.socket')
podman_socket=pathlib.Path(f'/run/user/{os.getuid()}/podman/podman.sock')
if not podman_socket.exists():
    raise RuntimeError('OpenShell Podman socket is unavailable')
stage='gateway-start'
run('systemctl','--user','restart','openshell-gateway')
for _ in range(30):
    ready=subprocess.run(['openshell','status'],stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    if ready.returncode == 0:
        break
    time.sleep(1)
else:
    raise RuntimeError('OpenShell gateway did not become ready')
"""

_VERIFY_OPENSHELL = r"""
import os, pathlib, shutil, subprocess

for command in ('openshell','openshell-gateway','podman'):
    if shutil.which(command) is None:
        raise RuntimeError('Prepared OpenShell host is missing required software')
version=subprocess.check_output(['podman','version','--format','{{.Client.Version}}'],text=True).strip()
if int(version.split('.',1)[0]) < 5:
    raise RuntimeError('Prepared OpenShell host requires Podman 5 or newer')
socket=pathlib.Path(f'/run/user/{os.getuid()}/podman/podman.sock')
if not socket.exists():
    raise RuntimeError('Prepared OpenShell host Podman socket is unavailable')
subprocess.run(['openshell','status'],check=True,stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL)
"""

_CONFIGURE_PROVIDERS = r"""
import json, os, pathlib, subprocess, sys

providers=json.load(sys.stdin)
for provider in providers:
    name=provider['name']
    existing=subprocess.run(
        ['openshell','provider','get',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL
    )
    if existing.returncode == 0:
        continue
    profile=pathlib.Path('/tmp')/(name+'.yaml')
    profile.write_text(provider['profile'])
    os.chmod(profile,0o600)
    try:
        known=subprocess.run(
            ['openshell','provider','profile','export',name],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        )
        if known.returncode:
            subprocess.run(
                ['openshell','provider','profile','import','--file',str(profile)],
                check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            )
        environment={**os.environ,provider['credential_env']:provider['credential_value']}
        provider['credential_value']=''
        subprocess.run(
            ['openshell','provider','create','--name',name,'--type',name,
             '--credential',provider['credential_env']],
            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=environment,
        )
        environment[provider['credential_env']]=''
    finally:
        profile.unlink(missing_ok=True)
"""

_START_OPENSHELL = r"""
import base64, json, os, pathlib, re, subprocess, sys
p=json.load(sys.stdin)
name=p['sandbox_name']
stage='lookup'
def report_stage(exc_type,exc_value,traceback):
    print('NIIU_OPEN_SHELL_STAGE:'+stage,file=sys.stderr)
    sys.__excepthook__(exc_type,exc_value,traceback)
sys.excepthook=report_stage
def run(*args, **kwargs):
    return subprocess.run(args,check=True,stdout=subprocess.DEVNULL,**kwargs)
unit=p['workload_unit']
subprocess.run(['systemctl','--user','stop',unit],stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL)
subprocess.run(['systemctl','--user','reset-failed',unit],stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL)
stage='workspace'
root=pathlib.Path('/var/lib/niuu/session')
workspace=root/'workspace'
home=root/'home'
run('sudo','-n','mkdir','-p',str(workspace),str(home))
stage='uid-map'
sandbox_owner=f"{p['sandbox_uid']}:{p['sandbox_gid']}"
# Restore and retry paths can replace the bind-mount ownership while the
# sandbox record remains.  Normalize through Podman's user namespace before
# both create and start so the image user always owns the writable mounts.
run('sudo','-n','chown','-R',f'{os.getuid()}:{os.getgid()}',
    str(workspace),str(home))
run('podman','unshare','chown','-R',sandbox_owner,str(workspace),str(home))
existing=subprocess.run(['openshell','sandbox','get',name],stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
if existing.returncode:
    # Ask Podman's own user namespace to translate the image identity to the
    # host subordinate-ID range.  The podman service PID is not guaranteed to
    # live in that namespace, so deriving IDs from /proc/<pid>/*_map can leave
    # bind mounts owned by nobody inside the sandbox.
    mounts=[
        {'type':'bind','source':str(workspace),
         'target':p['workspace_target'],'read_only':False},
        {'type':'bind','source':str(home),
         'target':p['home_target'],'read_only':False},
    ]
    stage='secret-staging'
    secret_root=root/'.openshell-secrets'
    run('sudo','-n','install','-d','-m','0700','-o',str(os.getuid()),
        '-g',str(os.getgid()),str(secret_root))
    for index,mount in enumerate(p.get('secret_mounts',[])):
        staged=secret_root/str(index)
        # The gateway must read the source as the host user while the sandbox
        # must see it owned by its image UID through the rootless user namespace.
        run('sudo','-n','install','-m','0640','-o',str(os.getuid()),
            '-g',str(os.getgid()),'--',mount['source'],str(staged))
        run('podman','unshare','chown',sandbox_owner,str(staged))
        mounts.append({'type':'bind','source':str(staged),
                       'target':mount['target'],'read_only':True})
    args=['openshell','sandbox','create','--name',name,
          '--from',p['image'],'--no-tty','--no-auto-providers']
    for provider in p.get('providers',[]):
        args.extend(['--provider',provider])
    args.extend(['--driver-config-json',json.dumps({'podman':{'mounts':mounts}})])
    for key,value in p['environment'].items():
        args.extend(['--env',key+'='+value])
    runtime_policy=None
    if p.get('policy_content'):
        runtime_policy=pathlib.Path('/tmp')/(name+'-policy.yaml')
        runtime_policy.write_text(p['policy_content'])
        os.chmod(runtime_policy,0o600)
        args.extend(['--policy',str(runtime_policy)])
    elif p.get('policy_file'):
        args.extend(['--policy',p['policy_file']])
    # OpenShell treats the create command as the sandbox's canonical process;
    # it must remain alive while Skuld is launched separately through exec.
    args.extend(['--','sleep','infinity'])
    stage='sandbox-create'
    created=subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    if runtime_policy is not None:
        runtime_policy.unlink(missing_ok=True)
    if created.returncode:
        message=created.stderr.lower()
        classifications=(
            ('cli',('unexpected argument','unknown argument','unrecognized option')),
            ('image-pull',('imagepullfailed','manifest unknown','pull image','unauthorized')),
            ('mount',('mount','bind source')),
            ('permissions',('permission denied','operation not permitted')),
            ('callback',('connection refused','deadline exceeded','health check','callback')),
            ('supervisor',('supervisor',)),
            ('podman',('podman',)),
        )
        category=next((label for label,needles in classifications
                       if any(needle in message for needle in needles)),'unknown')
        stage+='-'+category
        diagnostic=created.stderr
        for value in p.get('environment',{}).values():
            if value:
                diagnostic=diagnostic.replace(str(value),'<redacted>')
        diagnostic=re.sub(
            r'(?i)(authorization|bearer|api[_-]?key|token|password|secret)'
            r'(\s*[:=]\s*)\S+',r'\1\2<redacted>',diagnostic
        )
        diagnostic=re.sub(r'[A-Za-z0-9_+/=-]{32,}','<redacted>',diagnostic)
        diagnostic=' '.join(diagnostic.split())[:800]
        diagnostic=''.join(char if 32 <= ord(char) <= 126 else '?' for char in diagnostic)
        if diagnostic:
            encoded=base64.b64encode(diagnostic.encode()).decode()
            print('NIIU_OPEN_SHELL_DETAIL:'+encoded,file=sys.stderr)
        raise RuntimeError('OpenShell sandbox creation failed')
else:
    stage='sandbox-start'
    run('openshell','sandbox','start',name)
stage='workload-start'
run('systemd-run','--user','--unit',unit,'--collect',
    '--property','Restart=on-failure','--property','RestartSec=2s',
    'openshell','sandbox','exec','--name',name,'--no-tty','--',
    *p['sandbox_command'])
stage='forward'
subprocess.run(['openshell','forward','stop',str(p['broker_port'])],
               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
subprocess.run(['openshell','forward','start',str(p['broker_port']),name,'-d'],
               check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
"""

_STOP_OPENSHELL = r"""
import base64, json, pathlib, subprocess, sys, time
stage='preflight'
def report_stage(exc_type,exc_value,traceback):
    print('NIIU_OPEN_SHELL_STAGE:'+stage,file=sys.stderr)
    sys.__excepthook__(exc_type,exc_value,traceback)
sys.excepthook=report_stage
def classify_failure(stderr):
    diagnostic=(stderr or '').lower()
    categories=(
        ('deadline-exceeded',('deadline exceeded','deadlineexceeded','timed out','timeout')),
        ('transport',('transport error','connection error','connection refused',
                      'connection reset','connection closed','broken pipe','unavailable')),
        ('permission',('permission denied','permissiondenied','unauthenticated','forbidden')),
        ('conflict',('conflict','aborted','failed precondition','failedprecondition')),
    )
    return next((code for code,needles in categories
                 if any(needle in diagnostic for needle in needles)),'unknown')
def report_failure(code,message):
    encoded=base64.b64encode(code.encode()).decode()
    print('NIIU_OPEN_SHELL_DETAIL:'+encoded,file=sys.stderr)
    raise RuntimeError(message)
def bounded_run(argv,**kwargs):
    remaining=deadline-time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(argv,0)
    return subprocess.run(argv,timeout=min(command_timeout,remaining),**kwargs)
def sandbox_absent(name):
    INVENTORY_LIMIT=1000
    INVENTORY_MAX_PAGES=100
    offset=0
    for _ in range(INVENTORY_MAX_PAGES):
        inventory=bounded_run(
            ['openshell','sandbox','list','--limit',str(INVENTORY_LIMIT),
             '--offset',str(offset),
             '--output','json'],capture_output=True,text=True)
        if inventory.returncode != 0:
            return None,classify_failure(inventory.stderr)
        try:
            entries=json.loads(inventory.stdout)
        except (TypeError,json.JSONDecodeError):
            return None,'invalid-inventory'
        if not isinstance(entries,list) or any(
            not isinstance(item,dict) or not isinstance(item.get('name'),str)
            or not item['name'] for item in entries
        ):
            return None,'invalid-inventory'
        if any(item.get('name') == name for item in entries):
            return False,None
        if len(entries) < INVENTORY_LIMIT:
            return True,None
        offset+=len(entries)
    return None,'invalid-inventory'
allocation=sys.argv[1]
name=sys.argv[2]
unit=sys.argv[4]
marker=pathlib.Path('/var/lib/niuu/session/.allocation')
stage='allocation-marker'
if not marker.exists():
    sys.exit(42)
if marker.read_text()!=allocation:
    raise RuntimeError('Workspace allocation mismatch')
stage='forward-stop'
subprocess.run(['openshell','forward','stop',sys.argv[3]],stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL)
stage='workload-stop'
subprocess.run(['systemctl','--user','stop',unit],stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL)
stage='sandbox-delete'
timeout,poll_interval,command_timeout=map(float,sys.argv[6:9])
deadline=time.monotonic()+timeout
delete_exit='timeout'
delete_reason='deadline-exceeded'
try:
    deleted=bounded_run(['openshell','sandbox','delete',name],stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,text=True)
    delete_exit=str(deleted.returncode)
    delete_reason=classify_failure(deleted.stderr) if deleted.returncode else 'accepted'
except subprocess.TimeoutExpired:
    # The gateway owns its delete worker even if the CLI times out.
    pass
stage='sandbox-delete-verify'
checks=0
observation='not-checked'
presence='unknown'
while time.monotonic() < deadline:
    checks+=1
    try:
        absent,verify_failure=sandbox_absent(name)
    except subprocess.TimeoutExpired:
        absent,verify_failure=None,'deadline-exceeded'
    if absent is True:
        break
    if absent is False:
        presence='present'
    # Keep the last completed observation when the remaining deadline expires.
    # The separate deadline_exceeded field still reports the timeout.
    if verify_failure != 'deadline-exceeded' or observation == 'not-checked':
        observation=verify_failure or 'present'
    if verify_failure in ('permission','invalid-inventory'):
        break
    remaining=deadline-time.monotonic()
    if remaining > 0:
        time.sleep(min(poll_interval,remaining))
else:
    absent=False
if absent is not True:
    # Only controlled categories and numbers cross the guest diagnostic boundary.
    detail=(f'deletion-unconfirmed; delete_exit={delete_exit}; delete={delete_reason}; '
            f'inventory={observation}; last_presence={presence}; checks={checks}; '
            f'deadline_exceeded={time.monotonic() >= deadline}')
    report_failure(detail,'OpenShell sandbox deletion could not be verified before cleanup')
for provider in json.loads(sys.argv[5]):
    stage='provider-delete'
    subprocess.run(['openshell','provider','delete',provider],
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    stage='provider-delete-verify'
    remaining=subprocess.run(['openshell','provider','get',provider],
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if remaining.returncode == 0:
        raise RuntimeError('OpenShell provider credential deletion failed')
    stage='provider-profile-delete'
    subprocess.run(['openshell','provider','profile','delete',provider],
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
"""


class SshOpenShellVmRuntime(SshContainerVmRuntime):
    """Use official OpenShell on the allocated host instead of raw Docker."""

    def __init__(
        self,
        *,
        sandbox_platform_url: str = "",
        policy: str = "",
        sandbox_delete_timeout_seconds: float = 120,
        sandbox_delete_poll_interval_seconds: float = 2,
        sandbox_delete_command_timeout_seconds: float = 30,
        sandbox_uid: int = 998,
        sandbox_gid: int = 998,
        sandbox_command: list[str] | tuple[str, ...] = (
            "/usr/local/bin/openshell-run-installed-skuld",
        ),
        **kwargs,
    ):
        super().__init__(**kwargs)
        if min(sandbox_uid, sandbox_gid) < 1 or not sandbox_command:
            raise ValueError("OpenShell sandbox identity and command must be configured")
        deletion_timing = (
            sandbox_delete_timeout_seconds,
            sandbox_delete_poll_interval_seconds,
            sandbox_delete_command_timeout_seconds,
        )
        if any(not math.isfinite(value) or value <= 0 for value in deletion_timing):
            raise ValueError("OpenShell deletion timing must be finite and positive")
        self._sandbox_delete_timing = deletion_timing
        self._sandbox_platform_url = sandbox_platform_url.rstrip("/")
        self._policy = policy
        self._sandbox_uid = sandbox_uid
        self._sandbox_gid = sandbox_gid
        self._sandbox_command = tuple(sandbox_command)
        self._credential_store: CredentialStorePort | None = None

    def configure_credentials(self, credential_store: CredentialStorePort) -> None:
        self._credential_store = credential_store

    async def _execute_remote(
        self,
        lease: ComputeLease,
        bootstrap: MachineBootstrap,
        command: str,
        **kwargs,
    ) -> int:
        """Use the injected guest-access port; retain a narrow private-test seam."""
        if not hasattr(self, "_guest_access"):
            return await self._run([*self._ssh(lease, bootstrap), command], **kwargs)
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            command,
            **kwargs,
        )
        return result.exit_code

    @staticmethod
    def _user_python(program: str, *args: str) -> str:
        encoded = base64.b64encode(program.encode()).decode()
        code = f"import base64;exec(base64.b64decode({encoded!r}))"
        return shlex.join(["python3", "-c", code, *args])

    def session_bootstrap(
        self, session: Session, spec: SessionSpec, machine: MachineBootstrap
    ) -> MachineBootstrap:
        bootstrap = super().session_bootstrap(session, spec, machine)
        files = list(bootstrap.files)
        launch_index = next(index for index, item in enumerate(files) if item.path == _LAUNCH)
        payload = json.loads(files[launch_index].content)
        payload["environment"].update(
            {
                "SESSION_ID": str(session.id),
                "WORKSPACE_DIR": _OPEN_SHELL_WORKSPACE,
                "SKULD__SESSION__WORKSPACE_DIR": _OPEN_SHELL_WORKSPACE,
                "SKULD__PERSISTENCE_MOUNT_PATH": _OPEN_SHELL_HOME,
                "HOME": _OPEN_SHELL_HOME,
                "CODEX_HOME": _OPEN_SHELL_CODEX_HOME,
                "CLAUDE_CONFIG_DIR": _OPEN_SHELL_CLAUDE_HOME,
                "SKULD_BOOTSTRAP_FOREGROUND": "true",
            }
        )
        if self._sandbox_platform_url:
            payload["environment"]["SKULD__VOLUNDR_API_URL"] = self._sandbox_platform_url
        payload["openshell_credential_mappings"] = _credential_mappings(spec.values)
        if self._policy:
            # The sandbox lifecycle runs as the access principal, while session
            # bootstrap files live below a root-owned /etc/niuu.  Carry the
            # policy through the protected launch payload so _START_OPENSHELL
            # can create its short-lived, principal-owned policy file.
            payload["policy_content"] = self._policy
        files[launch_index] = files[launch_index].model_copy(
            update={"content": json.dumps(payload)}
        )
        return bootstrap.model_copy(update={"files": tuple(files)})

    async def _ensure_openshell(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        program = (
            _VERIFY_OPENSHELL if getattr(self, "_host_prepared", False) else _INSTALL_OPENSHELL
        )
        await self._execute_remote(
            lease,
            bootstrap,
            self._user_python(program),
            safe_error_prefix=_OPEN_SHELL_ERROR_PREFIX,
            safe_detail_prefix=_OPEN_SHELL_DETAIL_PREFIX,
        )

    async def warm(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        await super().prepare(lease, bootstrap)
        await self._ensure_openshell(lease, bootstrap)
        await self._guest_access.execute(
            lease,
            bootstrap,
            shlex.join(["podman", "pull", self._image]),
        )

    async def prepare(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        await super().prepare(lease, bootstrap)
        await self._ensure_openshell(lease, bootstrap)

    @staticmethod
    def _sandbox_name(lease: ComputeLease) -> str:
        # OpenShell routes sandbox names through a field capped at 19 characters.
        # Keep the allocation-derived name deterministic without sending the full
        # UUID (the previous ``niuu-<uuid>`` form was 41 characters).
        return "niuu-" + lease.id.hex[:14]

    @staticmethod
    def _workload_unit(lease: ComputeLease) -> str:
        return "niuu-skuld-" + str(lease.id)

    async def _configure_native_providers(
        self,
        lease: ComputeLease,
        bootstrap: MachineBootstrap,
        payload: dict[str, Any],
    ) -> list[str]:
        mappings = payload.pop("openshell_credential_mappings", [])
        specs = _native_provider_specs(
            str(lease.session_id), {"openshell": {"credentialMappings": mappings}}
        )
        if not specs:
            return []
        if self._policy:
            payload["policy_content"] = _policy_with_native_providers(self._policy, specs)
        if self._credential_store is None:
            raise RuntimeError("SSH OpenShell native providers require a credential store")
        resolved: dict[str, dict[str, str]] = {}
        for spec in specs:
            credential_name = spec["credential_name"]
            if credential_name not in resolved:
                values = await self._credential_store.get_value(
                    "user", lease.owner_id, credential_name
                )
                if values is None:
                    raise RuntimeError(
                        f"Credential {credential_name!r} is unavailable for OpenShell"
                    )
                resolved[credential_name] = values
            value = resolved[credential_name].get(spec["credential_field"])
            if not value:
                raise RuntimeError(
                    f"Credential {credential_name!r} lacks field {spec['credential_field']!r}"
                )
            spec["credential_value"] = value
        try:
            await self._execute_remote(
                lease,
                bootstrap,
                self._user_python(_CONFIGURE_PROVIDERS),
                data=json.dumps(specs).encode(),
            )
        finally:
            for spec in specs:
                spec.pop("credential_value", None)
        return [spec["name"] for spec in specs]

    async def start(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        if lease.session_id is None:
            raise ValueError("Cannot start an unbound guest")
        session_directory = self._data / str(lease.session_id)
        session_directory.mkdir(mode=0o700, exist_ok=True, parents=True)
        files = [
            item.model_dump()
            for item in bootstrap.files
            if item.path == _LAUNCH
            or item.path == "/etc/niuu/openshell-policy.yaml"
            or item.path.startswith("/etc/niuu/session-secrets/")
        ]
        await self._execute_remote(
            lease,
            bootstrap,
            self._python(_SESSION_FILES),
            data=json.dumps(files).encode(),
        )
        archive = session_directory / "session.tar"
        if archive.exists():
            with archive.open("rb") as source:
                await self._execute_remote(
                    lease,
                    bootstrap,
                    self._python(_PREPARE, str(lease.id), "restore"),
                    stdin=source,
                )
        else:
            await self._execute_remote(
                lease,
                bootstrap,
                self._python(_PREPARE, str(lease.id), "empty"),
            )
        await self.target(lease, bootstrap)
        payload = json.loads(next(item.content for item in bootstrap.files if item.path == _LAUNCH))
        payload["providers"] = await self._configure_native_providers(lease, bootstrap, payload)
        payload.update(
            {
                "sandbox_name": self._sandbox_name(lease),
                "broker_port": self._broker_port,
                "sandbox_uid": self._sandbox_uid,
                "sandbox_gid": self._sandbox_gid,
                "sandbox_command": self._sandbox_command,
                "workload_unit": self._workload_unit(lease),
                "workspace_target": _OPEN_SHELL_WORKSPACE,
                "home_target": _OPEN_SHELL_HOME,
            }
        )
        await self._execute_remote(
            lease,
            bootstrap,
            self._user_python(_START_OPENSHELL),
            data=json.dumps(payload).encode(),
            safe_error_prefix=_OPEN_SHELL_ERROR_PREFIX,
            safe_detail_prefix=_OPEN_SHELL_DETAIL_PREFIX,
        )

    async def stop(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        payload = json.loads(next(item.content for item in bootstrap.files if item.path == _LAUNCH))
        providers = [
            spec["name"]
            for spec in _native_provider_specs(
                str(lease.session_id),
                {
                    "openshell": {
                        "credentialMappings": payload.get("openshell_credential_mappings", [])
                    }
                },
                reject_files=False,
                resolve_targets=False,
            )
        ]
        result = await self._execute_remote(
            lease,
            bootstrap,
            self._user_python(
                _STOP_OPENSHELL,
                str(lease.id),
                self._sandbox_name(lease),
                str(self._broker_port),
                self._workload_unit(lease),
                json.dumps(providers),
                *(str(value) for value in self._sandbox_delete_timing),
            ),
            expected_exit_codes=(0, 42),
            safe_error_prefix=_OPEN_SHELL_ERROR_PREFIX,
            safe_detail_prefix=_OPEN_SHELL_DETAIL_PREFIX,
        )
        if result == 42:
            await self._close_tunnel(str(lease.id))
            return
        directory = self._data / str(lease.session_id or lease.id)
        directory.mkdir(mode=0o700, exist_ok=True, parents=True)
        descriptor, temporary = tempfile.mkstemp(prefix="session-", suffix=".tar", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as destination:
                await self._execute_remote(
                    lease,
                    bootstrap,
                    shlex.join(
                        [
                            "sudo",
                            "-n",
                            "tar",
                            "--one-file-system",
                            "--exclude=" + _CODEX_TMP_ARCHIVE_PATH,
                            "-C",
                            _REMOTE_DATA,
                            "-cf",
                            "-",
                            ".",
                        ]
                    ),
                    stdout=destination,
                )
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, directory / "session.tar")
        finally:
            Path(temporary).unlink(missing_ok=True)
        await self._close_tunnel(str(lease.id))
