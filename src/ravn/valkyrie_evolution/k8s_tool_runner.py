"""One Kubernetes Job per learned-tool invocation, with verified reach policy."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ravn.valkyrie_evolution.learned_tools import (
    DEFAULT_CONTAINED_TOOL_IMAGE,
    NETWORK_REACH_KINDS,
    REACH_ENFORCEMENT_ENFORCED,
    REACH_ENFORCEMENT_UNAVAILABLE,
    LearnedToolError,
    LearnedToolInfrastructureError,
)
from ravn.valkyrie_evolution.models import ToolReachGrant
from ravn.valkyrie_evolution.tool_runtime import HostCall, ToolRunResult
from ravn.valkyrie_evolution.tool_verification import (
    DEFAULT_VERIFY_TIMEOUT_SECONDS,
    TEST_RUNNER_SCRIPT,
    VerificationResult,
    first_undeclared_import,
    parse_missing_module,
    static_defects,
)

logger = logging.getLogger(__name__)

NETWORK_DENIED_LABEL = "denied"
NETWORK_ALLOWED_LABEL = "allowed"
DEFAULT_TOOL_RUN_IMAGE = DEFAULT_CONTAINED_TOOL_IMAGE
_MAX_BUNDLE_BYTES = 768 * 1024
_MAX_OUTPUT_BYTES = 256 * 1024
#: Applied to every Secret this executor creates, so an operator can find
#: leftovers (a crash before the finally-block delete ran) with a label
#: selector instead of grepping names.
_MANAGED_BY_LABEL = {"app.kubernetes.io/managed-by": "ravn-k8s-job-runner"}


@dataclass
class JobRunResult:
    """Raw outcome of one isolated Job."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    output_exceeded: bool = False
    network_enforced: bool = False


class JobExecutor(Protocol):
    """Execute one tool in a separate runtime boundary."""

    enforces_reach: bool

    async def execute(
        self,
        *,
        run_name: str,
        image: str,
        code: str,
        payload: dict[str, Any],
        entry_point: str,
        requirements: Sequence[str],
        timeout_seconds: float,
        network_allowed: bool,
    ) -> JobRunResult: ...

    async def execute_verification(
        self,
        *,
        run_name: str,
        image: str,
        tool_name: str,
        tool_code: str,
        test_code: str,
        requirements: Sequence[str],
        timeout_seconds: float,
    ) -> JobRunResult: ...


class KubernetesJobLearnedToolRunner:
    """Translate a learned-tool invocation into one verified Kubernetes Job."""

    #: No route from an ephemeral Job pod back to this process.
    supports_host_call = False
    #: verify() re-runs a peer's test_code in the SAME NetworkPolicy-verified,
    #: denied-network Job boundary run() uses (never on this resident's own
    #: host) — see :meth:`verify`. A peer's declared requirements are
    #: refused outright, the same posture run() takes at invocation time.
    supports_verify = True

    def __init__(self, *, executor: JobExecutor, image: str = DEFAULT_TOOL_RUN_IMAGE) -> None:
        self._executor = executor
        self._image = image

    @property
    def enforces_reach(self) -> bool:
        return bool(self._executor.enforces_reach)

    async def run(
        self,
        tool_path: Path,
        payload: dict[str, Any],
        *,
        entry_point: str,
        timeout_seconds: float,
        requirements: Sequence[str] = (),
        declared_reach: Sequence[ToolReachGrant] = (),
        host_call: HostCall | None = None,
    ) -> ToolRunResult:
        if host_call is not None:
            return ToolRunResult(
                ok=False,
                error=(
                    "this execution backend cannot provide the host SDK: the tool asks to "
                    "call the resident's own tools and there is no channel back from here. "
                    "Run it on the local backend, or rebuild it self-contained."
                ),
                infrastructure=True,
            )
        try:
            network_allowed = _network_reach(declared_reach)
            if requirements:
                raise LearnedToolError(
                    "k8s_job does not install packages at invocation time; use a reviewed "
                    "immutable runner image containing the dependency and declare no runtime "
                    "requirements"
                )
            code = Path(tool_path).read_text(encoding="utf-8")
            bundle_size = len(code.encode()) + len(json.dumps(payload, default=str).encode())
            if bundle_size > _MAX_BUNDLE_BYTES:
                raise LearnedToolError(
                    f"learned-tool bundle exceeds {_MAX_BUNDLE_BYTES} byte Kubernetes limit"
                )
        except (OSError, LearnedToolError, TypeError, ValueError) as exc:
            return ToolRunResult(
                ok=False,
                error=str(exc),
                enforcement=REACH_ENFORCEMENT_UNAVAILABLE,
                infrastructure=True,
            )

        try:
            run = await self._executor.execute(
                run_name=_run_name(Path(tool_path).stem),
                image=self._image,
                code=code,
                payload=payload,
                entry_point=entry_point,
                requirements=(),
                timeout_seconds=timeout_seconds,
                network_allowed=network_allowed,
            )
        except Exception as exc:  # noqa: BLE001 - infrastructure failure is evidence
            return ToolRunResult(
                ok=False,
                error=f"pod-per-run execution failed: {exc}",
                enforcement=REACH_ENFORCEMENT_UNAVAILABLE,
                infrastructure=True,
            )

        enforcement = (
            REACH_ENFORCEMENT_ENFORCED if run.network_enforced else REACH_ENFORCEMENT_UNAVAILABLE
        )
        if run.timed_out:
            return ToolRunResult(
                ok=False,
                error=f"learned tool timed out after {timeout_seconds}s",
                stderr=run.stderr,
                enforcement=enforcement,
            )
        if run.output_exceeded:
            return ToolRunResult(
                ok=False,
                error=f"learned tool output exceeded {_MAX_OUTPUT_BYTES} bytes",
                stderr=run.stderr,
                enforcement=enforcement,
            )
        if run.exit_code != 0:
            return ToolRunResult(
                ok=False,
                error=f"learned tool exited with status {run.exit_code}",
                stderr=run.stderr,
                enforcement=enforcement,
            )
        try:
            result = json.loads(run.stdout)
        except json.JSONDecodeError as exc:
            return ToolRunResult(
                ok=False,
                error=f"learned tool produced non-JSON output: {exc}",
                stderr=run.stderr,
                enforcement=enforcement,
            )
        if not isinstance(result, dict):
            return ToolRunResult(
                ok=False,
                error=f"learned tool must return a JSON object, got {type(result).__name__}",
                stderr=run.stderr,
                enforcement=enforcement,
            )
        return ToolRunResult(
            ok=True,
            result=result,
            stderr=run.stderr,
            enforcement=enforcement,
        )

    async def verify(
        self,
        *,
        tool_name: str,
        tool_code: str,
        test_code: str,
        requirements: Sequence[str] = (),
        entry_point: str = "run",
        timeout_seconds: float = DEFAULT_VERIFY_TIMEOUT_SECONDS,
    ) -> VerificationResult:
        """Independently re-verify a peer's tool_code/test_code in a fresh,
        NetworkPolicy-verified Kubernetes Job — the SAME boundary run() uses,
        never on this resident's own host.

        *requirements* are declined outright, the same posture as run():
        installing a peer's chosen packages would need its own throwaway
        Job with real egress (arbitrary sdist build code, the metadata
        endpoint, in-cluster services) for a tool that — on this backend —
        can never actually run with those requirements anyway (run() refuses
        them at invocation time), so there is nothing legitimate for that
        egress to buy. A tool needing dependencies belongs in a reviewed,
        pinned runner image instead.
        """
        del entry_point  # accepted for parity; the test module drives the run.
        defects = static_defects(tool_code, requirements)
        if defects:
            return VerificationResult(
                ok=False,
                logs="static verification failed:\n" + "\n".join(f"  - {d}" for d in defects),
                missing_module=first_undeclared_import(tool_code, requirements),
            )
        if requirements:
            return VerificationResult(
                ok=False,
                logs=(
                    "k8s_job requires dependencies baked into the runner image; it "
                    "does not install packages for run() or verify() — declare no "
                    "runtime requirements, or verify on the 'container' backend"
                ),
            )
        if not test_code.strip():
            return VerificationResult(
                ok=True,
                logs="no test_code supplied; structural validation only",
            )

        run_name = _verify_run_name(tool_name)
        try:
            run = await self._executor.execute_verification(
                run_name=run_name,
                image=self._image,
                tool_name=tool_name,
                tool_code=tool_code,
                test_code=test_code,
                requirements=(),
                timeout_seconds=timeout_seconds,
            )
        except LearnedToolInfrastructureError:
            raise
        except LearnedToolError as exc:
            raise LearnedToolInfrastructureError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - infrastructure failure is evidence
            raise LearnedToolInfrastructureError(
                f"k8s_job verification unavailable: {exc}"
            ) from exc

        return _verification_result_from_job(run, timeout_seconds=timeout_seconds)


#: Kubernetes object names are DNS-1123 labels: lowercase ASCII alphanumerics
#: and '-' only, max 63 characters. ``str.isalnum()`` is Unicode-aware and
#: accepts letters like 'é' or 'ツ', which the API rejects outright (a 422)
#: — surfacing as a permanent "infrastructure" failure for what is really a
#: naming bug. Only a hard ASCII allowlist is actually DNS-1123-safe.
_DNS1123_MAX_LENGTH = 63
_DNS1123_DISALLOWED_RE = re.compile(r"[^a-z0-9-]+")


def _k8s_safe_component(raw: str, *, max_len: int) -> str:
    safe = _DNS1123_DISALLOWED_RE.sub("-", raw.casefold()).strip("-")
    return safe[:max_len] or "tool"


def _verify_run_name(tool_name: str) -> str:
    name = f"ravn-verify-{_k8s_safe_component(tool_name, max_len=20)}-{uuid.uuid4().hex[:8]}"
    return name[:_DNS1123_MAX_LENGTH]


def _verification_result_from_job(
    run: JobRunResult, *, timeout_seconds: float
) -> VerificationResult:
    logs = run.stdout.strip()
    if run.timed_out:
        return VerificationResult(
            ok=False,
            logs=logs or f"verification timed out after {timeout_seconds}s",
        )
    if run.exit_code == 0:
        return VerificationResult(ok=True, logs=logs or "verification passed")
    return VerificationResult(
        ok=False,
        logs=logs or f"verification test exited with status {run.exit_code}",
        missing_module=parse_missing_module(logs),
    )


def _network_reach(declared_reach: Sequence[ToolReachGrant]) -> bool:
    network = False
    for grant in declared_reach:
        kind = grant.kind.casefold()
        if kind in {"", "pure_compute"} or grant.access == "none":
            continue
        is_network = kind in NETWORK_REACH_KINDS or kind.startswith("http")
        if not is_network:
            raise LearnedToolError(
                f"k8s_job cannot enforce declared reach kind {grant.kind!r}; refusing to run"
            )
        if grant.target.strip():
            raise LearnedToolError(
                "k8s_job cannot enforce target-specific network reach "
                f"({grant.target}); refusing to widen it to unrestricted egress"
            )
        if grant.access != "read_write":
            raise LearnedToolError(
                "k8s_job can enforce only broad network/read_write reach; "
                f"refusing to treat sockets as {grant.access!r}"
            )
        network = True
    return network


def _run_name(stem: str) -> str:
    name = f"ravn-tool-{_k8s_safe_component(stem, max_len=40)}-{uuid.uuid4().hex[:8]}"
    return name[:_DNS1123_MAX_LENGTH]


@dataclass(frozen=True)
class _ExecutorConfig:
    namespace: str
    image: str
    deny_policy_name: str
    allow_policy_name: str
    network_policy_label_key: str
    poll_interval_seconds: float = 1.0
    output_limit_bytes: int = _MAX_OUTPUT_BYTES
    job_cpu_request: str = "50m"
    job_memory_request: str = "64Mi"
    job_cpu_limit: str = "1"
    job_memory_limit: str = "512Mi"
    job_tmp_size: str = "64Mi"
    job_pod_start_timeout_seconds: float = 120.0
    job_ttl_seconds_after_finished: int = 3600


class KubernetesJobExecutor:
    """Create, observe, and remove locked-down Jobs using ``kubernetes_asyncio``.

    The executor verifies both configured NetworkPolicies through the live API
    immediately before every Job. A pod label alone is never treated as
    enforcement evidence.
    """

    def __init__(
        self,
        *,
        namespace: str,
        deny_policy_name: str,
        allow_policy_name: str,
        image: str = DEFAULT_TOOL_RUN_IMAGE,
        network_policy_label_key: str = "niuu.world/tool-network",
        job_cpu_request: str = "50m",
        job_memory_request: str = "64Mi",
        job_cpu_limit: str = "1",
        job_memory_limit: str = "512Mi",
        job_tmp_size: str = "64Mi",
        job_pod_start_timeout_seconds: float = 120.0,
        job_ttl_seconds_after_finished: int = 3600,
        batch_v1: Any | None = None,
        core_v1: Any | None = None,
        networking_v1: Any | None = None,
        in_cluster: bool = True,
    ) -> None:
        if not namespace.strip():
            raise LearnedToolError("k8s_job requires an explicit namespace")
        if not deny_policy_name.strip() or not allow_policy_name.strip():
            raise LearnedToolError("k8s_job requires explicit deny and allow policy names")
        if "@sha256:" not in image:
            raise LearnedToolError("k8s_job runner image must be pinned by sha256 digest")
        self._config = _ExecutorConfig(
            namespace=namespace,
            image=image,
            deny_policy_name=deny_policy_name,
            allow_policy_name=allow_policy_name,
            network_policy_label_key=network_policy_label_key,
            job_cpu_request=job_cpu_request,
            job_memory_request=job_memory_request,
            job_cpu_limit=job_cpu_limit,
            job_memory_limit=job_memory_limit,
            job_tmp_size=job_tmp_size,
            job_pod_start_timeout_seconds=job_pod_start_timeout_seconds,
            job_ttl_seconds_after_finished=job_ttl_seconds_after_finished,
        )
        self._batch_v1 = batch_v1
        self._core_v1 = core_v1
        self._networking_v1 = networking_v1
        self._in_cluster = in_cluster
        self._clients_loaded = all(
            client is not None for client in (batch_v1, core_v1, networking_v1)
        )
        self._policies_verified = False

    @property
    def enforces_reach(self) -> bool:
        """Whether a live policy check has succeeded for this executor."""
        return self._policies_verified

    async def execute(
        self,
        *,
        run_name: str,
        image: str,
        code: str,
        payload: dict[str, Any],
        entry_point: str,
        requirements: Sequence[str],
        timeout_seconds: float,
        network_allowed: bool,
    ) -> JobRunResult:
        if requirements:
            raise LearnedToolError("k8s_job runtime requirements must be empty")
        effective_image = image or self._config.image
        if "@sha256:" not in effective_image:
            raise LearnedToolError("k8s_job runner image must be pinned by sha256 digest")
        batch, core, networking = await self._load_clients()
        label = NETWORK_ALLOWED_LABEL if network_allowed else NETWORK_DENIED_LABEL
        await self._verify_network_policies(networking, network_label=label)
        created_secret = False
        created_job = False
        try:
            body = self._job_body(
                run_name=run_name,
                image=effective_image,
                command=["python", "-I", "-B", "-c", _BOOTSTRAP],
                network_label=label,
                active_deadline_seconds=timeout_seconds,
                env=[
                    {"name": "RAVN_TOOL_ENTRY", "value": entry_point},
                    {"name": "HOME", "value": "/tmp"},
                ],
                volume_mounts=[
                    {"name": "tool", "mountPath": "/tool", "readOnly": True},
                    {"name": "tmp", "mountPath": "/tmp"},
                ],
                volumes=[
                    {"name": "tool", "secret": {"secretName": run_name}},
                    {"name": "tmp", "emptyDir": {"sizeLimit": self._config.job_tmp_size}},
                ],
                container_name="tool",
            )
            # Job created BEFORE the Secret: creating it first gives the
            # Secret an ownerReference to the Job's real UID (a third,
            # independent GC backstop behind the explicit delete and the
            # Job's TTL). A Job never validates that a referenced Secret
            # already exists, so this ordering costs nothing.
            job = await batch.create_namespaced_job(self._config.namespace, body)
            created_job = True
            await self._create_secret(core, run_name, code, payload, owner_job_uid=_job_uid(job))
            created_secret = True
            timed_out = not await self._wait_for_completion(batch, run_name, timeout_seconds)
            logs = "" if timed_out else await self._read_pod_logs(core, run_name)
            output_exceeded = len(logs.encode()) > self._config.output_limit_bytes
            if output_exceeded:
                logs = logs.encode()[: self._config.output_limit_bytes].decode(errors="replace")
            exit_code = 124 if timed_out else await self._exit_code(batch, run_name)
            return JobRunResult(
                stdout=logs,
                stderr=logs if exit_code else "",
                exit_code=exit_code,
                timed_out=timed_out,
                output_exceeded=output_exceeded,
                network_enforced=self._policies_verified,
            )
        finally:
            await self._cleanup(
                batch,
                core,
                job_name=run_name if created_job else None,
                secret_name=run_name if created_secret else None,
            )

    async def execute_verification(
        self,
        *,
        run_name: str,
        image: str,
        tool_name: str,
        tool_code: str,
        test_code: str,
        requirements: Sequence[str],
        timeout_seconds: float,
    ) -> JobRunResult:
        """Re-verify a tool's test_code in the SAME NetworkPolicy-verified,
        denied-network Job boundary :meth:`execute` enforces for a tool with
        no network reach.

        *requirements* are refused here too — the caller
        (:meth:`KubernetesJobLearnedToolRunner.verify`) already declines a
        peer proposal with requirements before ever reaching the API; this
        is defense in depth, the same belt-and-suspenders check
        :meth:`execute` makes for itself.

        A hung test is distinguished from an infrastructure failure by
        whether the container ever started (see :meth:`_wait_verify_completion`):
        a hang is a failed :class:`~ravn.valkyrie_evolution.tool_verification.VerificationResult`,
        never a raised :class:`LearnedToolInfrastructureError` — a peer whose
        tests hang must be durably rejected, not retried forever on
        redelivery because every attempt "looks like" an outage.
        """
        del tool_name  # no longer used for naming; every verify file name is fixed.
        if requirements:
            raise LearnedToolError("k8s_job verification requirements must be empty")
        effective_image = image or self._config.image
        if "@sha256:" not in effective_image:
            raise LearnedToolError("k8s_job runner image must be pinned by sha256 digest")
        batch, core, networking = await self._load_clients()
        # verify() always runs its test Job denied-network, so the additive
        # check always applies here (unlike execute(), which only applies
        # it for a denied-network invocation).
        await self._verify_network_policies(networking, network_label=NETWORK_DENIED_LABEL)

        created_secret = False
        created_job = False
        try:
            body = self._job_body(
                run_name=run_name,
                image=effective_image,
                command=[
                    "python",
                    "/verify/_verify_runner.py",
                    "/verify/_verify_tool.py",
                    "/verify/_verify_test.py",
                ],
                network_label=NETWORK_DENIED_LABEL,
                # The test's own timeout only starts counting once the
                # container is actually running (see
                # _wait_verify_completion), so Kubernetes must not be able
                # to kill the Job for merely taking a while to *start* —
                # activeDeadlineSeconds has to cover both budgets.
                active_deadline_seconds=timeout_seconds
                + self._config.job_pod_start_timeout_seconds,
                env=[
                    {"name": "HOME", "value": "/tmp"},
                    {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                ],
                volume_mounts=[
                    {"name": "verify", "mountPath": "/verify", "readOnly": True},
                    {"name": "tmp", "mountPath": "/tmp"},
                ],
                volumes=[
                    {"name": "verify", "secret": {"secretName": run_name}},
                    {"name": "tmp", "emptyDir": {"sizeLimit": self._config.job_tmp_size}},
                ],
                container_name="verify",
            )
            # Job created BEFORE the Secret — see execute()'s comment for why.
            job = await batch.create_namespaced_job(self._config.namespace, body)
            created_job = True
            await self._create_verify_secret(
                core, run_name, tool_code, test_code, owner_job_uid=_job_uid(job)
            )
            created_secret = True
            timed_out = await self._wait_verify_completion(batch, core, run_name, timeout_seconds)
            logs = await self._read_pod_logs(core, run_name)
            output_exceeded = len(logs.encode()) > self._config.output_limit_bytes
            if output_exceeded:
                logs = logs.encode()[: self._config.output_limit_bytes].decode(errors="replace")
            exit_code = 124 if timed_out else await self._exit_code(batch, run_name)
            return JobRunResult(
                stdout=logs,
                exit_code=exit_code,
                timed_out=timed_out,
                output_exceeded=output_exceeded,
                network_enforced=self._policies_verified,
            )
        finally:
            await self._cleanup(
                batch,
                core,
                job_name=run_name if created_job else None,
                secret_name=run_name if created_secret else None,
            )

    async def _wait_verify_completion(
        self,
        batch: Any,
        core: Any,
        run_name: str,
        timeout_seconds: float,
    ) -> bool:
        """Wait for the verify Job to reach a terminal state.

        The test's own *timeout_seconds* budget starts counting from the
        container's actual ``state.running.startedAt`` — never from when
        this loop started polling — so a slow image pull never eats into
        the time the test itself gets to run (a 100s pull ahead of a 30s
        test must not become "ran for 0s, therefore hung").

        Returns ``True`` when the container ran for at least
        *timeout_seconds* without the Job reaching a terminal state — a
        genuine test hang, reported by the caller as a failed verification,
        never infrastructure. Raises :class:`LearnedToolInfrastructureError`
        when the container never started within its own
        ``job_pod_start_timeout_seconds`` budget (scheduling, image pull),
        or when ``_infra_failure_reason`` finds a definite cause once the
        Job is marked failed.
        """
        wait_start = time.monotonic()
        pod_start_deadline = wait_start + self._config.job_pod_start_timeout_seconds
        # Backstop only: activeDeadlineSeconds on the Job itself covers this
        # same combined budget, so Kubernetes kills the Job around when this
        # loop would give up anyway even if polling never observes a
        # terminal state or a startedAt this loop can act on.
        backstop_deadline = pod_start_deadline + timeout_seconds
        started_at: dt.datetime | None = None
        while True:
            job = await batch.read_namespaced_job(run_name, self._config.namespace)
            status = _job_status(job)
            if status.get("succeeded"):
                return False
            if status.get("failed"):
                reason = await self._infra_failure_reason(core, run_name)
                if reason:
                    raise LearnedToolInfrastructureError(
                        f"verification Job {run_name} never ran its container: {reason}"
                    )
                return False
            if started_at is None:
                started_at = await self._pod_started_at(core, run_name)
            now = time.monotonic()
            if started_at is None and now >= pod_start_deadline:
                reason = await self._infra_failure_reason(core, run_name)
                detail = f" ({reason})" if reason else ""
                raise LearnedToolInfrastructureError(
                    f"verification Job {run_name} pod did not start within "
                    f"{self._config.job_pod_start_timeout_seconds}s{detail}"
                )
            if started_at is not None:
                ran_for = (dt.datetime.now(dt.UTC) - started_at).total_seconds()
                if ran_for >= timeout_seconds:
                    return True
            if now >= backstop_deadline:
                reason = await self._infra_failure_reason(core, run_name)
                detail = f" ({reason})" if reason else ""
                raise LearnedToolInfrastructureError(
                    f"verification Job {run_name} did not complete within "
                    f"{timeout_seconds}s{detail}"
                )
            # Wake at whichever budget is actually relevant next: the
            # pod-start deadline while nothing has started yet, the
            # backstop once it has (ran_for is wall-clock and checked fresh
            # on the next iteration regardless of sleep granularity).
            next_deadline = backstop_deadline if started_at is not None else pod_start_deadline
            await asyncio.sleep(
                min(self._config.poll_interval_seconds, max(0.0, next_deadline - now))
            )

    async def _infra_failure_reason(self, core: Any, run_name: str) -> str | None:
        pods = await core.list_namespaced_pod(
            self._config.namespace, label_selector=f"job-name={run_name}"
        )
        items = getattr(pods, "items", pods)
        for pod in items:
            reason = _pod_infra_reason(pod)
            if reason:
                return reason
        return None

    async def _pod_started_at(self, core: Any, run_name: str) -> dt.datetime | None:
        pods = await core.list_namespaced_pod(
            self._config.namespace, label_selector=f"job-name={run_name}"
        )
        items = getattr(pods, "items", pods)
        for pod in items:
            started_at = _container_started_at(pod)
            if started_at is not None:
                return started_at
        return None

    async def _cleanup(
        self,
        batch: Any,
        core: Any,
        *,
        job_name: str | None,
        secret_name: str | None,
    ) -> None:
        """Best-effort teardown of exactly what was actually created.

        Shielded from cancellation so a caller that times out awaiting this
        call does not also abandon the delete calls mid-flight. A delete
        failure is logged loudly, never silently dropped — but it is never
        raised here: this runs in a ``finally`` around a result or error
        that already has something real to say, and a teardown failure must
        not override it. A leaked Job or Secret is a problem worth paging
        on (their TTL is the backstop), not one worth losing the actual
        verification/run outcome over.
        """
        # Each entry is (what failed to delete, coroutine). The Secret is
        # described by its owning Job rather than by its own resource name.
        cleanups: list[tuple[str, Any]] = []
        if job_name:
            cleanups.append((f"Job {job_name!r}", self._delete_job(batch, job_name)))
        if secret_name:
            cleanups.append(
                (f"payload Secret of Job {job_name!r}", self._delete_secret(core, secret_name))
            )
        if not cleanups:
            return
        results = await asyncio.shield(
            asyncio.gather(*(coro for _, coro in cleanups), return_exceptions=True)
        )
        for (what, _), result in zip(cleanups, results, strict=True):
            if isinstance(result, Exception):
                logger.error("k8s_job cleanup failed to delete %s: %s", what, result)

    async def _create_verify_secret(
        self,
        core: Any,
        name: str,
        tool_code: str,
        test_code: str,
        *,
        owner_job_uid: str,
    ) -> None:
        await core.create_namespaced_secret(
            self._config.namespace,
            {
                "metadata": {
                    "name": name,
                    "labels": dict(_MANAGED_BY_LABEL),
                    "ownerReferences": _owner_references(name=name, uid=owner_job_uid),
                },
                "stringData": {
                    # Fixed names, never derived from the peer-controlled
                    # title: TEST_RUNNER_SCRIPT already registers the tool
                    # module as "_verify_tool" in sys.modules regardless of
                    # its on-disk path, so a title-derived file name bought
                    # nothing and let a tool titled "_verify_test" collide
                    # with the test file itself (last write wins in a dict
                    # literal), silently running the test module in place of
                    # the tool it was supposed to verify.
                    "_verify_tool.py": tool_code,
                    "_verify_test.py": test_code,
                    "_verify_runner.py": TEST_RUNNER_SCRIPT,
                },
                "type": "Opaque",
            },
        )

    def _job_body(
        self,
        *,
        run_name: str,
        image: str,
        command: list[str],
        network_label: str,
        active_deadline_seconds: float,
        env: list[dict[str, str]],
        volume_mounts: list[dict[str, Any]],
        volumes: list[dict[str, Any]],
        container_name: str,
    ) -> dict[str, Any]:
        """The one locked-down Job/pod shape used by both :meth:`execute`
        and :meth:`execute_verification` — resource limits, security
        context, and TTL come from the SAME config on both paths, so they
        can never quietly drift apart from each other.

        *active_deadline_seconds* is the Kubernetes-enforced hard ceiling —
        callers size it to their OWN polling budget (verify()'s must cover
        both the pod-start budget and the test timeout, since the test
        timeout only starts counting once the container is actually
        running) so Kubernetes never kills the Job out from under a polling
        loop that has not yet reached its own conclusion.
        """
        labels = {self._config.network_policy_label_key: network_label}
        body: dict[str, Any] = {
            "metadata": {"name": run_name, "labels": labels},
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": max(1, math.ceil(active_deadline_seconds)),
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "restartPolicy": "Never",
                        "automountServiceAccountToken": False,
                        "enableServiceLinks": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 1000,
                            "runAsGroup": 1000,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": container_name,
                                "image": image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": command,
                                "env": env,
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "runAsNonRoot": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "resources": {
                                    "requests": {
                                        "cpu": self._config.job_cpu_request,
                                        "memory": self._config.job_memory_request,
                                    },
                                    "limits": {
                                        "cpu": self._config.job_cpu_limit,
                                        "memory": self._config.job_memory_limit,
                                    },
                                },
                                "volumeMounts": volume_mounts,
                            }
                        ],
                        "volumes": volumes,
                    },
                },
            },
        }
        if self._config.job_ttl_seconds_after_finished > 0:
            body["spec"]["ttlSecondsAfterFinished"] = self._config.job_ttl_seconds_after_finished
        return body

    async def _load_clients(self) -> tuple[Any, Any, Any]:
        if self._clients_loaded:
            return self._batch_v1, self._core_v1, self._networking_v1
        from kubernetes_asyncio import client, config  # noqa: PLC0415

        if self._in_cluster:
            config.load_incluster_config()
        else:
            await config.load_kube_config()
        self._batch_v1 = client.BatchV1Api()
        self._core_v1 = client.CoreV1Api()
        self._networking_v1 = client.NetworkingV1Api()
        self._clients_loaded = True
        return self._batch_v1, self._core_v1, self._networking_v1

    async def _verify_network_policies(self, networking: Any, *, network_label: str) -> None:
        """Verify the two named policies by name, and — only for a
        denied-network invocation — that no OTHER policy in the namespace
        also grants denied pods egress.

        The additive check is skipped for an allowed-network Job: such a
        pod is supposed to have egress, so there is nothing for an extra
        policy to silently widen, and running it unconditionally would
        require the `list` RBAC verb and could break every learned-tool
        invocation in a namespace that happens to carry an unrelated
        namespace-wide policy (e.g. "allow DNS for everyone") — a real
        upgrade hazard for a check that, for an allowed pod, protects
        nothing.
        """
        self._policies_verified = False
        deny = await networking.read_namespaced_network_policy(
            self._config.deny_policy_name, self._config.namespace
        )
        allow = await networking.read_namespaced_network_policy(
            self._config.allow_policy_name, self._config.namespace
        )
        if not _policy_matches(
            deny,
            label_key=self._config.network_policy_label_key,
            label_value=NETWORK_DENIED_LABEL,
            allow_egress=False,
        ):
            raise LearnedToolError(
                f"NetworkPolicy {self._config.deny_policy_name!r} does not deny all egress "
                "for learned-tool denied pods"
            )
        if not _policy_matches(
            allow,
            label_key=self._config.network_policy_label_key,
            label_value=NETWORK_ALLOWED_LABEL,
            allow_egress=True,
        ):
            raise LearnedToolError(
                f"NetworkPolicy {self._config.allow_policy_name!r} does not allow egress "
                "for learned-tool allowed pods"
            )
        if network_label == NETWORK_DENIED_LABEL:
            await self._verify_no_additional_denied_egress(networking)
        self._policies_verified = True

    async def _verify_no_additional_denied_egress(self, networking: Any) -> None:
        """NetworkPolicies are additive: a correctly-shaped deny policy is
        void if some OTHER policy in the namespace also selects denied pods
        and grants them any egress — a namespace-wide "allow DNS" policy
        selecting every pod is a realistic example. Enumerate every policy
        in the namespace and refuse to proceed if one is found, rather than
        trusting that reading the two named policies by name tells the
        whole story."""
        policies = await networking.list_namespaced_network_policy(self._config.namespace)
        items = getattr(policies, "items", policies)
        for policy in items:
            data = policy.to_dict() if hasattr(policy, "to_dict") else policy
            if not isinstance(data, dict):
                continue
            name = str((data.get("metadata") or {}).get("name") or "")
            if name in {self._config.deny_policy_name, self._config.allow_policy_name}:
                continue
            if _policy_grants_egress_to_denied_pods(
                data, label_key=self._config.network_policy_label_key
            ):
                raise LearnedToolError(
                    f"NetworkPolicy {name!r} also selects learned-tool denied pods (label "
                    f"{self._config.network_policy_label_key}={NETWORK_DENIED_LABEL!r}) and "
                    "grants them egress; NetworkPolicies are additive, so this silently "
                    "widens the deny policy beyond what was just verified — remove or "
                    "rescope it before enabling k8s_job"
                )

    async def _create_secret(
        self,
        core: Any,
        run_name: str,
        code: str,
        payload: dict[str, Any],
        *,
        owner_job_uid: str,
    ) -> None:
        await core.create_namespaced_secret(
            self._config.namespace,
            {
                "metadata": {
                    "name": run_name,
                    "labels": dict(_MANAGED_BY_LABEL),
                    "ownerReferences": _owner_references(name=run_name, uid=owner_job_uid),
                },
                "stringData": {"tool.py": code, "payload.json": json.dumps(payload)},
                "type": "Opaque",
            },
        )

    async def _wait_for_completion(self, batch: Any, run_name: str, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while True:
            job = await batch.read_namespaced_job(run_name, self._config.namespace)
            status = _job_status(job)
            if status.get("succeeded") or status.get("failed"):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(self._config.poll_interval_seconds, remaining))

    async def _read_pod_logs(self, core: Any, run_name: str) -> str:
        pods = await core.list_namespaced_pod(
            self._config.namespace, label_selector=f"job-name={run_name}"
        )
        items = getattr(pods, "items", pods)
        if not items:
            return ""
        return await core.read_namespaced_pod_log(
            _pod_name(items[0]),
            self._config.namespace,
            limit_bytes=self._config.output_limit_bytes + 1,
        )

    async def _exit_code(self, batch: Any, run_name: str) -> int:
        status = _job_status(await batch.read_namespaced_job(run_name, self._config.namespace))
        return 0 if status.get("succeeded") else 1

    async def _delete_job(self, batch: Any, run_name: str) -> None:
        await batch.delete_namespaced_job(
            run_name,
            self._config.namespace,
            propagation_policy="Background",
            grace_period_seconds=0,
        )

    async def _delete_secret(self, core: Any, run_name: str) -> None:
        await core.delete_namespaced_secret(run_name, self._config.namespace)


def _policy_matches(
    policy: Any,
    *,
    label_key: str,
    label_value: str,
    allow_egress: bool,
) -> bool:
    """True when *policy* is shaped exactly as the deny/allow contract
    requires: a selector of precisely ``{matchLabels: {label_key:
    label_value}}`` with no ``matchExpressions`` and no extra matchLabels
    keys. A selector with extras (another required label, say) selects
    FEWER pods than all learned-tool pods carrying *label_value* — some of
    them would then carry no matching NetworkPolicy at all, silently
    unprotected, while :attr:`enforces_reach` reports protection."""
    data = policy.to_dict() if hasattr(policy, "to_dict") else policy
    if not isinstance(data, dict):
        return False
    spec = data.get("spec") or {}
    selector = spec.get("pod_selector") or spec.get("podSelector") or {}
    labels = selector.get("match_labels") or selector.get("matchLabels") or {}
    expressions = selector.get("match_expressions") or selector.get("matchExpressions") or []
    if labels != {label_key: label_value} or expressions:
        return False
    policy_types = spec.get("policy_types") or spec.get("policyTypes") or []
    if not {"Ingress", "Egress"}.issubset(policy_types):
        return False
    if spec.get("ingress"):
        return False
    egress = spec.get("egress")
    if not allow_egress:
        return not egress
    return isinstance(egress, list) and any(_is_allow_all_rule(rule) for rule in egress)


def _is_allow_all_rule(rule: Any) -> bool:
    data = rule.to_dict() if hasattr(rule, "to_dict") else rule
    return isinstance(data, dict) and not (data.get("ports") or data.get("to"))


#: Labels the Job controller itself adds to every pod it creates from a
#: template, in addition to whatever labels the template's own metadata
#: sets — present on every learned-tool pod, but with a per-run value
#: (the Job's name/uid) that cannot be known in advance.
_JOB_CONTROLLER_LABEL_KEYS = frozenset(
    {
        "job-name",
        "batch.kubernetes.io/job-name",
        "controller-uid",
        "batch.kubernetes.io/controller-uid",
    }
)


def _selector_term_matches_denied_pod(
    key: str,
    operator: str,
    values: Sequence[str],
    *,
    label_key: str,
) -> bool:
    """Whether one ``matchExpressions`` term (or a `matchLabels` entry
    modeled as ``operator="In"``) is satisfied by a learned-tool denied
    pod's actual, guaranteed label set: exactly *label_key* = "denied",
    plus the Job-controller-injected keys (present, value unknowable in
    advance) — every other key is genuinely absent, since the Job body sets
    no other pod labels. An operator this code does not recognise, or a
    dynamic-key value comparison it cannot resolve without knowing the
    Job's name, is treated as MATCHING: failing open here would let an
    unrecognised selector shape silently widen egress past what the deny
    policy was verified to enforce.
    """
    if key == label_key:
        if operator == "In":
            return NETWORK_DENIED_LABEL in values
        if operator == "NotIn":
            return NETWORK_DENIED_LABEL not in values
        if operator == "Exists":
            return True
        if operator == "DoesNotExist":
            return False
        return True  # unknown operator: fail closed
    if key in _JOB_CONTROLLER_LABEL_KEYS:
        if operator == "Exists":
            return True
        if operator == "DoesNotExist":
            return False
        return True  # In/NotIn/unknown against an unknowable value: fail closed
    # Any other key: the pod template sets no other label, so it is
    # genuinely absent — standard Kubernetes selector semantics for an
    # absent key: In/Exists fail, NotIn/DoesNotExist are satisfied.
    if operator == "In":
        return False
    if operator == "NotIn":
        return True
    if operator == "Exists":
        return False
    if operator == "DoesNotExist":
        return True
    return True  # unknown operator: fail closed


def _selector_matches_denied_pod(selector: dict[str, Any], *, label_key: str) -> bool:
    """True when *selector* (a NetworkPolicy's ``podSelector``) would select
    a learned-tool denied pod. An empty selector matches every pod in the
    namespace. Every matchLabels entry and every matchExpressions term must
    hold (Kubernetes selectors AND their terms together)."""
    labels = selector.get("match_labels") or selector.get("matchLabels") or {}
    expressions = selector.get("match_expressions") or selector.get("matchExpressions") or []
    if not labels and not expressions:
        return True
    labels_match = all(
        _selector_term_matches_denied_pod(key, "In", [value], label_key=label_key)
        for key, value in labels.items()
    )
    expressions_match = all(
        _selector_term_matches_denied_pod(
            expr.get("key") or "",
            expr.get("operator") or "",
            expr.get("values") or [],
            label_key=label_key,
        )
        for expr in expressions
    )
    return labels_match and expressions_match


def _policy_grants_egress_to_denied_pods(data: dict[str, Any], *, label_key: str) -> bool:
    """True when *data* (an arbitrary NetworkPolicy, not one of the two
    verified by name) would ALSO apply to learned-tool denied pods and
    grant them some egress. NetworkPolicies are additive in Kubernetes, so
    this is what an unrelated namespace-wide policy (e.g. "allow DNS for
    everyone") would need to look like to silently widen the deny policy's
    egress beyond what :func:`_policy_matches` verified."""
    spec = data.get("spec") or {}
    policy_types = spec.get("policy_types") or spec.get("policyTypes") or []
    egress = spec.get("egress")
    # An omitted policyTypes defaults (per the Kubernetes API) to governing
    # Egress whenever the policy carries any egress rules at all.
    applies_to_egress = "Egress" in policy_types or (not policy_types and bool(egress))
    if not applies_to_egress or not egress:
        return False
    selector = spec.get("pod_selector") or spec.get("podSelector") or {}
    return _selector_matches_denied_pod(selector, label_key=label_key)


def _job_status(job: Any) -> dict[str, Any]:
    status = getattr(job, "status", job)
    if isinstance(status, dict):
        return status
    return {
        "succeeded": getattr(status, "succeeded", None),
        "failed": getattr(status, "failed", None),
    }


def _job_uid(job: Any) -> str:
    """The server-assigned UID of a just-created Job, or "" if unknown."""
    metadata = getattr(job, "metadata", None)
    if metadata is not None:
        return str(getattr(metadata, "uid", "") or "")
    if isinstance(job, dict):
        return str((job.get("metadata") or {}).get("uid") or "")
    return ""


def _owner_references(*, name: str, uid: str) -> list[dict[str, Any]]:
    """An ownerReference pointing a Secret at its Job, so the Kubernetes
    garbage collector removes the Secret when the Job is deleted — a third,
    independent backstop behind the explicit post-run delete and the Job's
    own TTL, covering the case where both of those never ran (a crash
    between creating the Secret and the Job finishing). Omitted when *uid*
    is unavailable rather than failing the run over a best-effort cleanup
    hint; the explicit delete and TTL remain the primary and secondary
    mechanisms either way.
    """
    if not uid:
        return []
    return [
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "name": name,
            "uid": uid,
            "controller": True,
            "blockOwnerDeletion": True,
        }
    ]


def _pod_name(pod: Any) -> str:
    metadata = getattr(pod, "metadata", None)
    if metadata is not None:
        return str(getattr(metadata, "name", ""))
    return str(pod.get("metadata", {}).get("name", "")) if isinstance(pod, dict) else ""


#: Container "waiting" reasons that mean the tool/test code never started —
#: an infrastructure failure, never a verdict on the code under test.
_INFRA_WAITING_REASONS = frozenset(
    {
        "ImagePullBackOff",
        "ErrImagePull",
        "InvalidImageName",
        "CreateContainerConfigError",
        "CreateContainerError",
    }
)


#: An eviction message is treated as infrastructure only when it names a
#: cluster-driven cause. A peer whose own test writes past its ephemeral-
#: storage/emptyDir/memory limit is evicted for ITS OWN resource usage — a
#: failed verification, not an outage. Defaulting every eviction to
#: infrastructure let such a peer force retries forever instead of a
#: durable rejection, so the default here is "not infrastructure" and only
#: a positively-identified cluster-driven cause overrides it.
_INFRA_EVICTION_MARKERS = ("drain", "preempt", "taint")


def _eviction_is_infrastructure(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _INFRA_EVICTION_MARKERS)


def _pod_infra_reason(pod: Any) -> str | None:
    """A human-readable reason the pod never ran (or was involuntarily
    removed from) its container for a cluster-driven cause, or None.

    None covers "still fine, keep waiting", "ran and exited on its own",
    and an eviction caused by the pod's own resource usage — consulted once
    a Job has failed, once its pod-start budget has elapsed without the
    container starting, or once the overall deadline has passed with the
    container never having started, so it never masks a genuine test
    failure or a genuine hang as infrastructure.
    """
    data = pod.to_dict() if hasattr(pod, "to_dict") else pod
    if not isinstance(data, dict):
        return None
    status = data.get("status") or {}
    if status.get("reason") == "Evicted":
        message = status.get("message") or "no reason given"
        if _eviction_is_infrastructure(message):
            return f"evicted: {message}"
        return None
    for condition in status.get("conditions") or []:
        if (
            condition.get("type") == "PodScheduled"
            and condition.get("status") == "False"
            and condition.get("reason") == "Unschedulable"
        ):
            return f"unschedulable: {condition.get('message') or 'no reason given'}"
        if condition.get("type") == "DisruptionTarget":
            detail = condition.get("message") or condition.get("reason") or "pod disrupted"
            return f"disruption target: {detail}"
    container_statuses = status.get("container_statuses") or status.get("containerStatuses") or []
    for container_status in container_statuses:
        waiting = (container_status.get("state") or {}).get("waiting") or {}
        if waiting.get("reason") in _INFRA_WAITING_REASONS:
            message = waiting.get("message") or ""
            return f"{waiting.get('reason')}: {message}".rstrip(": ")
    if status.get("phase") == "Pending" and not container_statuses:
        return "pod never started (still Pending with no container status)"
    return None


def _container_started_at(pod: Any) -> dt.datetime | None:
    """The timestamp the pod's container actually entered ``running`` (or
    is already ``terminated``, for a container that finished between two
    polls) — never inferred from pod ``phase`` alone, since a kubelet
    admission refusal (``OutOfcpu``, ``NodeAffinity``) can put a pod in
    phase ``Failed`` without its container ever having run at all. Used to
    start the test's own timeout budget from when the code under test
    actually began executing, not from Job creation or from whenever this
    process happened to poll."""
    data = pod.to_dict() if hasattr(pod, "to_dict") else pod
    if not isinstance(data, dict):
        return None
    status = data.get("status") or {}
    container_statuses = status.get("container_statuses") or status.get("containerStatuses") or []
    for container_status in container_statuses:
        state = container_status.get("state") or {}
        for key in ("running", "terminated"):
            started_at = (state.get(key) or {}).get("started_at") or (state.get(key) or {}).get(
                "startedAt"
            )
            if started_at is None:
                continue
            if isinstance(started_at, dt.datetime):
                return started_at if started_at.tzinfo else started_at.replace(tzinfo=dt.UTC)
            if isinstance(started_at, str):
                try:
                    return dt.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                except ValueError:
                    continue
    return None


_BOOTSTRAP = """\
import importlib.util
import json
import os

spec = importlib.util.spec_from_file_location("learned_tool", "/tool/tool.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with open("/tool/payload.json", encoding="utf-8") as handle:
    payload = json.load(handle)
result = getattr(module, os.environ["RAVN_TOOL_ENTRY"])(payload)
print(json.dumps(result), end="")
"""


__all__ = [
    "JobExecutor",
    "JobRunResult",
    "KubernetesJobExecutor",
    "KubernetesJobLearnedToolRunner",
]
