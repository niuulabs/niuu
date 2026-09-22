"""Executable proof for the file-backed SSH host preparation runner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import signal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from volundr.adapters.outbound.ssh_guest_access import SshGuestAccess
from volundr.adapters.outbound.ssh_host_preparation import (
    _STAGE_RUNNER,
    SshHostPreparation,
)
from volundr.domain.compute import MachineBootstrap
from volundr.domain.execution_catalog import (
    AdapterBinding,
    HostRecipe,
    HostRecipeStage,
    ScriptArtifact,
    ScriptMediaType,
)
from volundr.domain.guest_access import GuestAccess, GuestCommandResult
from volundr.domain.host_preparation import HostPreparationError


class LocalGuestAccess(GuestAccess):
    """Run the fixed guest program locally while preserving its stdin contract."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root
        self.ensure_calls = 0
        self.close_calls = 0

    @property
    def principal(self) -> str:
        return "tester"

    def configure_trust(self, *, principal: str, mode: str, kwargs: dict) -> None:
        assert principal == self.principal

    def validate_host_preparation(self) -> None:
        return None

    def machine_bootstrap(self, defaults: MachineBootstrap) -> MachineBootstrap:
        return defaults

    async def ensure(self, lease, bootstrap) -> None:
        self.ensure_calls += 1

    async def execute(
        self,
        lease,
        bootstrap,
        command: str,
        *,
        data: bytes | None = None,
        stdin=None,
        stdout=None,
        capture_stdout: bool = False,
        timeout_seconds: float | None = None,
        expected_exit_codes: tuple[int, ...] = (0,),
        safe_error_prefix: str | None = None,
        safe_detail_prefix: str | None = None,
    ) -> GuestCommandResult:
        payload = json.loads(data or b"{}")
        payload["state_root"] = str(self.state_root)
        argv = shlex.split(command)
        assert argv[:2] == ["sudo", "-n"]
        process = await asyncio.create_subprocess_exec(
            *argv[2:],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        async with asyncio.timeout(timeout_seconds):
            captured, stderr = await process.communicate(json.dumps(payload).encode())
        if process.returncode not in expected_exit_codes:
            SshGuestAccess._raise_command_error(
                process.returncode,
                stderr,
                safe_error_prefix=safe_error_prefix,
                safe_detail_prefix=safe_detail_prefix,
            )
        return GuestCommandResult(process.returncode, captured if capture_stdout else b"")

    async def open_tunnel(self, lease, bootstrap, **kwargs):
        raise AssertionError("host preparation does not open runtime tunnels")

    async def close_tunnel(self, lease_id: str) -> None:
        return None

    async def close(self) -> None:
        self.close_calls += 1


def _artifact(path: Path, artifact_id: str, content: str) -> ScriptArtifact:
    path.write_text(content)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return ScriptArtifact(
        id=artifact_id,
        package_path=path.name,
        resolved_path=path.resolve(),
        sha256=digest,
        media_type=ScriptMediaType.SHELL,
    )


def _recipe(
    tmp_path: Path,
    *,
    failing_apply: bool = False,
    secret: bool = False,
    long_apply: bool = False,
    timeout_seconds: float = 5,
) -> HostRecipe:
    marker = tmp_path / "installed"
    count = tmp_path / "apply-count"
    check = _artifact(
        tmp_path / "check.sh",
        "check",
        f"#!/bin/sh\ntest -f {shlex.quote(str(marker))}\n",
    )
    apply_exit = "exit 7" if failing_apply else "exit 0"
    secret_line = "echo 'password=short' >&2" if secret else ":"
    long_line = (
        f"echo $$ > {shlex.quote(str(tmp_path / 'child-pid'))}\n"
        f"touch {shlex.quote(str(tmp_path / 'child-started'))}\nsleep 30"
        if long_apply
        else ":"
    )
    apply = _artifact(
        tmp_path / "apply.sh",
        "apply",
        "#!/bin/sh\n"
        f"echo applied >> {shlex.quote(str(count))}\n"
        f"touch {shlex.quote(str(marker))}\n"
        f"{secret_line}\n{long_line}\n{apply_exit}\n",
    )
    verify = _artifact(
        tmp_path / "verify.sh",
        "verify",
        f"#!/bin/sh\ntest -f {shlex.quote(str(marker))} || exit 1\n"
        'printf \'{"engine":"ready"}\'\n',
    )
    return HostRecipe(
        id="test-recipe",
        revision="v1",
        digest="a" * 64,
        preparation=AdapterBinding(
            binding_id="ssh-preparation",
            adapter=("volundr.adapters.outbound.ssh_host_preparation.SshHostPreparation"),
        ),
        artifacts=(check, apply, verify),
        stages=(
            HostRecipeStage(
                id="engine",
                principal="root",
                timeout_seconds=timeout_seconds,
                check="check",
                apply="apply",
                verify="verify",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_runner_applies_once_then_rechecks_and_uses_durable_checkpoint(tmp_path: Path):
    access = LocalGuestAccess(tmp_path / "guest-state")
    preparation = SshHostPreparation(guest_access=access)
    recipe = _recipe(tmp_path)
    lease = SimpleNamespace(id=uuid4())

    first = await preparation.ensure(lease, MachineBootstrap(), recipe)
    second = await preparation.ensure(lease, MachineBootstrap(), recipe)

    assert first.stages[0].applied is True
    assert second.stages[0].applied is False
    assert second.facts == {"engine": "ready"}
    second.verify_requirements({"engine": "ready"})
    with pytest.raises(HostPreparationError, match="do not satisfy"):
        second.verify_requirements({"engine": "missing"})
    assert (tmp_path / "apply-count").read_text().splitlines() == ["applied"]
    state = json.loads(
        next((tmp_path / "guest-state" / "allocations").glob("*/state.json")).read_text()
    )
    assert state["recipe_digest"] == recipe.digest
    guest_artifact = next((tmp_path / "guest-state" / "artifacts").iterdir())
    assert guest_artifact.stat().st_mode & 0o777 == 0o755
    assert access.ensure_calls == 2


@pytest.mark.asyncio
async def test_restart_after_apply_side_effect_does_not_repeat_installer(tmp_path: Path):
    access = LocalGuestAccess(tmp_path / "guest-state")
    preparation = SshHostPreparation(guest_access=access)
    recipe = _recipe(tmp_path, failing_apply=True, secret=True)
    lease = SimpleNamespace(id=uuid4())

    with pytest.raises(HostPreparationError) as failure:
        await preparation.ensure(lease, MachineBootstrap(), recipe)
    assert "password=short" not in str(failure.value)

    # Recovery observes the side effect and never executes the failing installer again.
    result = await preparation.ensure(lease, MachineBootstrap(), recipe)

    assert result.stages[0].applied is False
    assert (tmp_path / "apply-count").read_text().splitlines() == ["applied"]


@pytest.mark.asyncio
async def test_observe_reports_drift_without_applying(tmp_path: Path):
    preparation = SshHostPreparation(guest_access=LocalGuestAccess(tmp_path / "guest-state"))
    recipe = _recipe(tmp_path)

    with pytest.raises(HostPreparationError, match="check-failed"):
        await preparation.observe(SimpleNamespace(id=uuid4()), MachineBootstrap(), recipe)

    assert not (tmp_path / "apply-count").exists()


def test_recipe_binding_principal_and_artifact_integrity_are_enforced(tmp_path: Path):
    access = LocalGuestAccess(tmp_path / "guest-state")
    with pytest.raises(ValueError, match="must be positive"):
        SshHostPreparation(guest_access=access, verification_output_limit_bytes=0)
    preparation = SshHostPreparation(guest_access=access)
    recipe = _recipe(tmp_path)

    wrong_binding = recipe.model_copy(
        update={
            "preparation": recipe.preparation.model_copy(
                update={"adapter": "tests.WrongPreparation"}
            )
        }
    )
    with pytest.raises(ValueError, match="not this preparation adapter"):
        preparation.machine_bootstrap(wrong_binding, MachineBootstrap())

    wrong_principal = recipe.model_copy(
        update={"stages": (recipe.stages[0].model_copy(update={"principal": "another-user"}),)}
    )
    with pytest.raises(ValueError, match="neither root nor access principal"):
        preparation.machine_bootstrap(wrong_principal, MachineBootstrap())

    recipe.artifacts[0].resolved_path.write_text("changed")
    with pytest.raises(ValueError, match="artifact digest mismatch"):
        preparation.machine_bootstrap(recipe, MachineBootstrap())


def test_runner_inherits_remote_lock_into_stage_process():
    assert "pass_fds=(lock.fileno(),)" in _STAGE_RUNNER
    assert "result=subprocess.run(command" in _STAGE_RUNNER
    assert "close_fds=True" in _STAGE_RUNNER
    assert "timeout=remaining" in _STAGE_RUNNER
    assert "start_new_session=True" in _STAGE_RUNNER
    assert "os.killpg(process.pid,signal.SIGKILL)" in _STAGE_RUNNER
    assert "os.killpg(os.getpgrp(),signal.SIGKILL)" in _STAGE_RUNNER
    assert "stderr=subprocess.DEVNULL" in _STAGE_RUNNER
    assert "password=" not in _STAGE_RUNNER


@pytest.mark.asyncio
async def test_allocation_lock_survives_controller_death_while_stage_runs(tmp_path: Path):
    preparation = SshHostPreparation(guest_access=LocalGuestAccess(tmp_path / "unused"))
    recipe = _recipe(tmp_path, long_apply=True)
    lease = SimpleNamespace(id=uuid4())
    payload = preparation._payload(lease, recipe, apply=True)
    payload["state_root"] = str(tmp_path / "guest-state")
    argv = shlex.split(preparation._python_command(_STAGE_RUNNER))[2:]

    first = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert first.stdin is not None
    first.stdin.write(json.dumps(payload).encode())
    await first.stdin.drain()
    first.stdin.close()
    for _ in range(200):
        if (tmp_path / "child-started").exists():
            break
        await asyncio.sleep(0.01)
    assert (tmp_path / "child-started").exists()
    child_pid = int((tmp_path / "child-pid").read_text())
    supervisor_group = os.getpgid(child_pid)
    assert supervisor_group != child_pid

    first.kill()
    await first.wait()
    second = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await second.communicate(json.dumps(payload).encode())
    try:
        assert second.returncode != 0
        assert b"NIIU_HOST_PREPARATION_STAGE:allocation-lock" in stderr
    finally:
        os.killpg(supervisor_group, signal.SIGKILL)


@pytest.mark.asyncio
async def test_supervisor_releases_lock_at_deadline_after_controller_death(tmp_path: Path):
    preparation = SshHostPreparation(guest_access=LocalGuestAccess(tmp_path / "unused"))
    recipe = _recipe(tmp_path, long_apply=True, timeout_seconds=0.4)
    lease = SimpleNamespace(id=uuid4())
    payload = preparation._payload(lease, recipe, apply=True)
    payload["state_root"] = str(tmp_path / "guest-state")
    argv = shlex.split(preparation._python_command(_STAGE_RUNNER))[2:]

    first = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert first.stdin is not None
    first.stdin.write(json.dumps(payload).encode())
    await first.stdin.drain()
    first.stdin.close()
    for _ in range(200):
        if (tmp_path / "child-started").exists():
            break
        await asyncio.sleep(0.01)
    assert (tmp_path / "child-started").exists()
    child_pid = int((tmp_path / "child-pid").read_text())
    supervisor_group = os.getpgid(child_pid)

    first.kill()
    await first.wait()
    await asyncio.sleep(0.6)

    retry = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        retry.communicate(json.dumps(payload).encode()), timeout=2
    )

    assert retry.returncode == 0, stderr.decode()
    assert json.loads(stdout)["facts"] == {"engine": "ready"}
    with pytest.raises(ProcessLookupError):
        os.killpg(supervisor_group, 0)


@pytest.mark.asyncio
async def test_separate_allocations_publish_shared_artifacts_atomically(tmp_path: Path):
    state_root = tmp_path / "guest-state"
    recipe = _recipe(tmp_path)
    first = SshHostPreparation(guest_access=LocalGuestAccess(state_root))
    second = SshHostPreparation(guest_access=LocalGuestAccess(state_root))

    results = await asyncio.gather(
        first.ensure(SimpleNamespace(id=uuid4()), MachineBootstrap(), recipe),
        second.ensure(SimpleNamespace(id=uuid4()), MachineBootstrap(), recipe),
    )

    assert all(result.recipe_digest == recipe.digest for result in results)
    artifacts = list((state_root / "artifacts").iterdir())
    assert len(artifacts) == 3
    expected = {artifact.sha256 for artifact in recipe.artifacts}
    assert {path.name for path in artifacts} == expected
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == path.name for path in artifacts)


@pytest.mark.asyncio
async def test_preparer_does_not_close_injected_access(tmp_path: Path):
    access = LocalGuestAccess(tmp_path / "guest-state")
    preparation = SshHostPreparation(guest_access=access)

    await preparation.close()

    assert access.close_calls == 0
