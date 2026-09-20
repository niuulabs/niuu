"""Execute pinned, file-backed host recipes through authenticated guest access."""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
from pathlib import Path

from volundr.domain.compute import ComputeLease, MachineBootstrap
from volundr.domain.execution_catalog import HostRecipe, ScriptArtifact, ScriptMediaType
from volundr.domain.guest_access import GuestAccess
from volundr.domain.host_preparation import (
    HostPreparation,
    HostPreparationError,
    HostPreparationResult,
)
from volundr.domain.vm_runtime import VmRuntimeStageError

_ERROR_PREFIX = "NIIU_HOST_PREPARATION_STAGE:"
_DETAIL_PREFIX = "NIIU_HOST_PREPARATION_DETAIL:"

_STAGE_RUNNER = r"""
import base64, fcntl, hashlib, json, os, pathlib, signal, subprocess, sys, tempfile, time

p=json.load(sys.stdin)
root=pathlib.Path(p['state_root'])
allocation=root/'allocations'/p['allocation_id']
lock_path=root/'locks'/(p['allocation_id']+'.lock')
lock_path.parent.mkdir(parents=True,exist_ok=True)
os.chmod(root,0o755)
os.chmod(lock_path.parent,0o755)
lock=lock_path.open('a+')
try:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
    print('NIIU_HOST_PREPARATION_STAGE:allocation-lock',file=sys.stderr)
    raise RuntimeError('Host preparation is already running for this allocation')

allocation.mkdir(parents=True,exist_ok=True)
os.chmod(allocation,0o700)
state_path=allocation/'state.json'
if state_path.exists():
    state=json.loads(state_path.read_text())
    if state.get('recipe_digest') != p['recipe_digest']:
        raise RuntimeError('Allocation host recipe digest mismatch')
    if not isinstance(state.get('stages'),dict):
        raise RuntimeError('Invalid host preparation checkpoint')
else:
    state={'recipe_digest':p['recipe_digest'],'stages':{}}

artifact_root=root/'artifacts'
artifact_root.mkdir(parents=True,exist_ok=True)
os.chmod(artifact_root,0o755)
artifacts={}
for item in p['artifacts']:
    content=base64.b64decode(item['content'],validate=True)
    if hashlib.sha256(content).hexdigest()!=item['sha256']:
        raise RuntimeError('Transferred host recipe artifact digest mismatch')
    path=artifact_root/item['sha256']
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
        raise RuntimeError('Guest host recipe artifact digest mismatch')
    if not path.exists():
        descriptor,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.niuu-tmp',dir=artifact_root)
        temporary=pathlib.Path(name)
        try:
            with os.fdopen(descriptor,'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary,0o755)
            os.replace(temporary,path)
        finally:
            temporary.unlink(missing_ok=True)
    os.chmod(path,0o755)
    artifacts[item['id']]={'path':path,'media_type':item['media_type']}

def atomic_state():
    temporary=state_path.with_name(state_path.name+'.niuu-tmp')
    with temporary.open('w') as stream:
        json.dump(state,stream,sort_keys=True,separators=(',',':'))
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary,0o600)
    os.replace(temporary,state_path)
    directory=os.open(allocation,os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)

def command_for(artifact,principal):
    if artifact['media_type']=='text/x-shellscript':
        command=['/bin/sh',str(artifact['path'])]
    else:
        command=[str(artifact['path'])]
    if principal!='root':
        command=['sudo','-n','-u',principal,'--',*command]
    return command

supervisor_program='''
import json, os, signal, subprocess, sys, time
command=json.loads(sys.argv[1])
deadline=float(sys.argv[2])
remaining=deadline-time.monotonic()
if remaining<=0:
    os.killpg(os.getpgrp(),signal.SIGKILL)
try:
    result=subprocess.run(command,stdin=subprocess.DEVNULL,check=False,
                          close_fds=True,timeout=remaining)
except subprocess.TimeoutExpired:
    os.killpg(os.getpgrp(),signal.SIGKILL)
sys.exit(result.returncode)
'''

def run(artifact_id,stage,deadline,capture=False):
    remaining=deadline-time.monotonic()
    if remaining<=0:
        raise TimeoutError('Host preparation stage timed out')
    with tempfile.TemporaryFile() as output:
        process=subprocess.Popen(
            [sys.executable,'-c',supervisor_program,
             json.dumps(command_for(artifacts[artifact_id],stage['principal'])),
             str(deadline)],
            stdin=subprocess.DEVNULL,
            stdout=output if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(lock.fileno(),),
            start_new_session=True,
        )
        try:
            returncode=process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise TimeoutError('Host preparation stage timed out')
        if returncode==-signal.SIGKILL and time.monotonic()>=deadline:
            raise TimeoutError('Host preparation stage timed out')
        content=b''
        if capture:
            output.seek(0)
            content=output.read(p['verification_output_limit_bytes']+1)
            if len(content)>p['verification_output_limit_bytes']:
                raise ValueError('Verification output exceeded configured limit')
        return returncode,content

results=[]
facts={}
for stage in p['stages']:
    stage_id=stage['id']
    phase='check'
    try:
        checkpoint=state['stages'].get(stage_id)
        if checkpoint is not None and (
            not isinstance(checkpoint,dict) or
            checkpoint.get('stage_digest')!=stage['stage_digest'] or
            checkpoint.get('status')!='verified'
        ):
            raise RuntimeError('Host preparation checkpoint mismatch')
        deadline=time.monotonic()+stage['timeout_seconds']
        checked,_=run(stage['check'],stage,deadline)
        applied=False
        if checked==1:
            if not p['apply']:
                raise RuntimeError('Host preparation stage requires apply')
            applied=True
            phase='apply'
            changed,_=run(stage['apply'],stage,deadline)
            if changed:
                raise RuntimeError('Host preparation apply failed')
        elif checked:
            raise RuntimeError('Host preparation check failed')
        phase='verify'
        verified,verification_output=run(stage['verify'],stage,deadline,capture=True)
        if verified:
            raise RuntimeError('Host preparation verify failed')
        stage_facts={}
        if verification_output.strip():
            phase='facts'
            stage_facts=json.loads(verification_output)
            if not isinstance(stage_facts,dict):
                raise RuntimeError('Verification facts must be a JSON object')
            if any(not isinstance(key,str) or not isinstance(value,str)
                   for key,value in stage_facts.items()):
                raise RuntimeError('Verification facts must map strings to strings')
        checkpoint={
            'stage_id':stage_id,
            'stage_digest':stage['stage_digest'],
            'status':'verified',
            'applied':applied,
            'facts':stage_facts,
        }
        state['stages'][stage_id]=checkpoint
        atomic_state()
        for key,value in stage_facts.items():
            if key in facts and facts[key]!=value:
                phase='facts'
                raise RuntimeError('Conflicting verification facts')
            facts[key]=value
        results.append(checkpoint)
    except BaseException as exc:
        print('NIIU_HOST_PREPARATION_STAGE:'+stage_id,file=sys.stderr)
        if isinstance(exc,TimeoutError):
            code='timeout'
        elif phase=='facts':
            code='invalid-facts'
        else:
            code=phase+'-failed'
        print('NIIU_HOST_PREPARATION_DETAIL:'+base64.b64encode(code.encode()).decode(),file=sys.stderr)
        raise

print(json.dumps({'recipe_digest':p['recipe_digest'],'stages':results,'facts':facts},separators=(',',':')))
"""


class SshHostPreparation(HostPreparation):
    """SSH preparation using packaged check/apply/verify stage artifacts."""

    def __init__(
        self,
        *,
        guest_access: GuestAccess,
        verification_output_limit_bytes: int = 65536,
    ) -> None:
        if verification_output_limit_bytes < 1:
            raise ValueError("verification_output_limit_bytes must be positive")
        guest_access.validate_host_preparation()
        self._access = guest_access
        self._verification_output_limit_bytes = verification_output_limit_bytes

    def machine_bootstrap(self, recipe: HostRecipe, defaults: MachineBootstrap) -> MachineBootstrap:
        self._validate_recipe(recipe)
        return self._access.machine_bootstrap(defaults)

    async def ensure(
        self,
        lease: ComputeLease,
        bootstrap: MachineBootstrap,
        recipe: HostRecipe,
    ) -> HostPreparationResult:
        return await self._run_recipe(lease, bootstrap, recipe, apply=True)

    async def observe(
        self,
        lease: ComputeLease,
        bootstrap: MachineBootstrap,
        recipe: HostRecipe,
    ) -> HostPreparationResult:
        return await self._run_recipe(lease, bootstrap, recipe, apply=False)

    async def _run_recipe(
        self,
        lease: ComputeLease,
        bootstrap: MachineBootstrap,
        recipe: HostRecipe,
        *,
        apply: bool,
    ) -> HostPreparationResult:
        payload = self._payload(lease, recipe, apply=apply)
        await self._access.ensure(lease, bootstrap)
        command = self._python_command(_STAGE_RUNNER)
        timeout = sum(stage.timeout_seconds for stage in recipe.stages)
        try:
            result = await self._access.execute(
                lease,
                bootstrap,
                command,
                data=json.dumps(payload, separators=(",", ":")).encode(),
                capture_stdout=True,
                timeout_seconds=timeout,
                safe_error_prefix=_ERROR_PREFIX,
                safe_detail_prefix=_DETAIL_PREFIX,
            )
            return HostPreparationResult.model_validate_json(result.stdout)
        except HostPreparationError:
            raise
        except VmRuntimeStageError as exc:
            suffix = f" ({exc.diagnostic})" if exc.diagnostic else ""
            raise HostPreparationError(
                f"Host preparation failed at stage {exc.stage}{suffix}"
            ) from exc
        except Exception as exc:
            raise HostPreparationError(str(exc)) from exc

    def _payload(self, lease: ComputeLease, recipe: HostRecipe, *, apply: bool) -> dict:
        self._validate_recipe(recipe)
        artifacts = {artifact.id: artifact for artifact in recipe.artifacts}
        return {
            "allocation_id": str(lease.id),
            "recipe_digest": recipe.digest,
            "apply": apply,
            "verification_output_limit_bytes": self._verification_output_limit_bytes,
            "state_root": "/var/lib/niuu/host-preparation",
            "artifacts": [self._artifact_payload(artifact) for artifact in recipe.artifacts],
            "stages": [
                {
                    "id": stage.id,
                    "principal": self._stage_principal(stage.principal),
                    "timeout_seconds": stage.timeout_seconds,
                    "check": stage.check,
                    "apply": stage.apply,
                    "verify": stage.verify,
                    "stage_digest": self._stage_digest(stage.model_dump(), artifacts),
                }
                for stage in recipe.stages
            ],
        }

    def _validate_recipe(self, recipe: HostRecipe) -> None:
        if recipe.preparation.adapter != f"{type(self).__module__}.{type(self).__qualname__}":
            raise ValueError(
                f"Host recipe {recipe.id!r} selects {recipe.preparation.adapter!r}, "
                "not this preparation adapter"
            )
        for artifact in recipe.artifacts:
            content = self._read_verified(artifact.resolved_path, artifact.sha256)
            if not content:
                raise ValueError(f"Host recipe artifact {artifact.id!r} is empty")
            if artifact.media_type not in {ScriptMediaType.EXECUTABLE, ScriptMediaType.SHELL}:
                raise ValueError(f"Unsupported host recipe artifact type {artifact.media_type!r}")
        for stage in recipe.stages:
            self._stage_principal(stage.principal)

    def _stage_principal(self, principal: str) -> str:
        if principal == "root":
            return principal
        if principal in {"access", self._access.principal}:
            return self._access.principal
        raise ValueError(
            f"Host recipe principal {principal!r} is neither root nor access principal "
            f"{self._access.principal!r}"
        )

    @classmethod
    def _artifact_payload(cls, artifact: ScriptArtifact) -> dict[str, str]:
        content = cls._read_verified(artifact.resolved_path, artifact.sha256)
        return {
            "id": artifact.id,
            "sha256": artifact.sha256,
            "media_type": artifact.media_type.value,
            "content": base64.b64encode(content).decode(),
        }

    @staticmethod
    def _read_verified(path: Path, expected: str) -> bytes:
        content = path.read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected:
            raise ValueError(f"Host recipe artifact digest mismatch for {path.name!r}")
        return content

    @staticmethod
    def _stage_digest(stage: dict, artifacts: dict[str, ScriptArtifact]) -> str:
        value = {
            "id": stage["id"],
            "principal": stage["principal"],
            "timeout_seconds": stage["timeout_seconds"],
            "artifacts": {
                role: artifacts[stage[role]].sha256 for role in ("check", "apply", "verify")
            },
        }
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _python_command(program: str) -> str:
        encoded = base64.b64encode(program.encode()).decode()
        code = f"import base64;exec(base64.b64decode({encoded!r}))"
        return shlex.join(["sudo", "-n", "python3", "-c", code])

    async def close(self) -> None:
        """The composition root owns the injected guest access collaborator."""
