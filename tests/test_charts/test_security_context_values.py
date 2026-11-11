"""Every service chart renders its security contexts from values.

The UID, non-root and fsGroup defaults used to be written into the templates,
with ``podSecurityContext`` appended after them. An override then produced a
duplicate key, and a field could never be dropped, so a cluster whose admission
policy rejects an explicit UID could not run the services at all. The defaults
now live in each chart's values and the templates render the block from there,
so setting a field to ``null`` removes it.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

CHARTS_DIR = Path(__file__).parent.parent.parent / "charts"

WORKLOAD_KINDS = {"Deployment", "StatefulSet", "Job", "CronJob"}

# chart -> (args that render every workload carrying a security context,
#           value paths that hold a runAsUser)
CHARTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "bifrost": (("--set", "envoy.enabled=true"), ("podSecurityContext",)),
    "mimir": (
        ("--set", "envoy.enabled=true"),
        ("podSecurityContext", "envoy.securityContext"),
    ),
    "gbrain": (
        (
            "--set",
            "dream.enabled=true",
            "--set",
            "engine=postgres",
            "--set",
            "postgres.enabled=true",
            "--set",
            "existingSecret=x",
        ),
        ("podSecurityContext", "postgres.databaseInit.podSecurityContext"),
    ),
    "guild": (("--set", "envoy.enabled=true"), ("envoy.securityContext",)),
    "observatory": (("--set", "envoy.enabled=true"), ("envoy.securityContext",)),
    "niuu-shared": (("--set", "envoy.enabled=true"), ("envoy.securityContext",)),
    "ting": (("--set", "envoy.enabled=true"), ("envoy.securityContext",)),
    "volundr": (
        (
            "--set",
            "envoy.enabled=true",
            "--set",
            "web.enabled=true",
            "--set",
            "webNext.enabled=true",
            "--set",
            "bifrost.url=http://bifrost",
        ),
        (
            "podSecurityContext",
            "securityContext",
            "envoy.securityContext",
            "web.podSecurityContext",
            "web.securityContext",
            "webNext.podSecurityContext",
            "webNext.securityContext",
        ),
    ),
}


def _render(chart: str, *extra: str) -> list[dict]:
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("helm is not installed")
    result = subprocess.run(
        [helm, "template", "test", str(CHARTS_DIR / chart), *CHARTS[chart][0], *extra],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if isinstance(d, dict)]


def _pod_specs(documents: list[dict]) -> Iterator[tuple[str, dict]]:
    for document in documents:
        if document.get("kind") not in WORKLOAD_KINDS:
            continue
        template = document["spec"]
        if document["kind"] == "CronJob":
            template = template["jobTemplate"]["spec"]
        yield document["metadata"]["name"], template["template"]["spec"]


def _security_contexts(documents: list[dict]) -> Iterator[tuple[str, dict]]:
    for name, pod in _pod_specs(documents):
        yield name, pod.get("securityContext") or {}
        for container in pod.get("initContainers", []) + pod.get("containers", []):
            yield f"{name}/{container['name']}", container.get("securityContext") or {}


@pytest.mark.parametrize("chart", sorted(CHARTS))
def test_defaults_keep_a_non_root_uid(chart: str) -> None:
    contexts = dict(_security_contexts(_render(chart)))

    assert any(ctx.get("runAsUser") for ctx in contexts.values()), contexts


@pytest.mark.parametrize("chart", sorted(CHARTS))
def test_every_uid_can_be_dropped(chart: str) -> None:
    drop = [arg for path in CHARTS[chart][1] for arg in ("--set", f"{path}.runAsUser=null")]

    contexts = dict(_security_contexts(_render(chart, *drop)))

    with_uid = {name: ctx["runAsUser"] for name, ctx in contexts.items() if "runAsUser" in ctx}
    assert with_uid == {}


@pytest.mark.parametrize("chart", ["bifrost", "mimir", "gbrain", "volundr"])
def test_pod_uid_override_replaces_the_default(chart: str) -> None:
    pods = dict(_pod_specs(_render(chart, "--set", "podSecurityContext.runAsUser=4242")))

    main = next(pod for name, pod in pods.items() if name == f"test-{chart}" or name == "test")
    assert main["securityContext"]["runAsUser"] == 4242


@pytest.mark.parametrize("chart", ["guild", "observatory", "niuu-shared"])
def test_pod_security_context_is_overridable(chart: str) -> None:
    pods = dict(_pod_specs(_render(chart, "--set", "podSecurityContext.fsGroup=4242")))

    assert pods[f"test-{chart}"]["securityContext"] == {"fsGroup": 4242}
