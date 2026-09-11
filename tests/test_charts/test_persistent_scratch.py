"""Verify actual scratch mounts, isolation and reuse in both Kubernetes launchers."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.conftest import make_spec
from volundr.adapters.outbound.direct_k8s_pod_manager import DirectK8sPodManager
from volundr.domain.models import GitSource, Session

CHART = Path(__file__).resolve().parents[2] / "charts/skuld"


def render(tmp_path, session_id, extras=()):
    values = tmp_path / "values.yaml"
    values.write_text(
        yaml.safe_dump(
            {
                "session": {"id": session_id},
                "homeVolume": {
                    "enabled": True,
                    "existingClaim": "owner-home",
                    "persistentTmp": True,
                },
                "extraContainers": list(extras),
            }
        )
    )
    result = subprocess.run(
        ["helm", "template", "scratch", str(CHART), "-f", str(values)],
        capture_output=True,
        text=True,
        check=True,
    )
    return next(
        d["spec"]["template"]["spec"]
        for d in yaml.safe_load_all(result.stdout)
        if d and d.get("kind") == "Deployment"
    )


def scratch(container):
    return {
        m["mountPath"]: m
        for m in container["volumeMounts"]
        if m["mountPath"] in ("/tmp", "/var/cache/niuu")
    }


def test_chart_isolates_temp_and_reuses_user_cache(tmp_path):
    extras = [
        {"name": f"ravn-{name}", "image": "busybox", "env": []}
        for name in ("coder", "reviewer", "security")
    ]
    pod = render(tmp_path, "session-one", extras)
    containers = [c for c in pod["containers"] if c["name"] != "nginx"]
    paths = set()
    for c in containers:
        mounts = scratch(c)
        assert mounts["/tmp"]["name"] == "home"
        assert mounts["/tmp"]["subPath"] == f"tmp/sessions/session-one/{c['name']}"
        paths.add(mounts["/tmp"]["subPath"])
        assert mounts["/var/cache/niuu"]["subPath"] == "tmp/cache"
        env = {e["name"]: e.get("value") for e in c["env"]}
        assert env["GOMODCACHE"] == "/var/cache/niuu/go-mod"
    assert len(paths) == len(containers)
    home_volume = next(v for v in pod["volumes"] if v["name"] == "home")
    assert home_volume["persistentVolumeClaim"]["claimName"] == "owner-home"

    # Execute the real init script twice: retained files survive restart.
    home = tmp_path / "home"
    init = next(c for c in pod["initContainers"] if c["name"] == "scratch-setup")
    assert init["securityContext"]["runAsUser"] == 0
    assert 'chown -h "$TARGET_OWNER"' in init["args"][0]
    script = init["args"][0].replace("/home/", f"{home}/")
    script = script.replace("1000:1000", f"{os.getuid()}:{os.getgid()}")
    subprocess.run(["sh", "-ec", script], check=True)
    retained = home / "tmp/sessions/session-one/ravn-coder/build-output"
    retained.write_text("keep")
    subprocess.run(["sh", "-ec", script], check=True)
    assert retained.read_text() == "keep"
    assert (retained.parent.stat().st_mode & 0o7777) == 0o1777
    second = render(tmp_path, "session-two", extras)
    coder = next(c for c in second["containers"] if c["name"] == "ravn-coder")
    assert scratch(coder)["/tmp"]["subPath"] not in paths
    assert scratch(coder)["/var/cache/niuu"]["subPath"] == "tmp/cache"


@pytest.mark.parametrize("bad_id", ["", "../other", 'x";touch /tmp/bad;#'])
def test_chart_rejects_unsafe_scratch_identity(tmp_path, bad_id):
    with pytest.raises(subprocess.CalledProcessError):
        render(tmp_path, bad_id)


def test_chart_rejects_conflicting_tmp_mount(tmp_path):
    with pytest.raises(subprocess.CalledProcessError):
        render(
            tmp_path,
            "session",
            [
                {
                    "name": "ravn-coder",
                    "image": "busybox",
                    "volumeMounts": [{"name": "other", "mountPath": "/tmp"}],
                }
            ],
        )


def test_direct_launcher_mounts_home_scratch_without_git():
    session = Session(name="scratch", model="claude", source=GitSource(repo="", branch="main"))
    spec = make_spec(
        homeVolume={
            "enabled": True,
            "existingClaim": "owner-home",
            "persistentTmp": True,
        }
    )
    pod = DirectK8sPodManager()._build_deployment_manifest(session, spec)["spec"]["template"][
        "spec"
    ]
    init = pod["initContainers"][1]
    assert init["name"] == "scratch-setup"
    assert init["securityContext"]["runAsUser"] == 0
    assert "chown -h 1000:1000" in init["command"][2]
    for c in pod["containers"]:
        if c["name"] == "nginx":
            continue
        mounts = scratch(c)
        assert mounts["/tmp"]["subPath"] == f"tmp/sessions/{session.id}/{c['name']}"
        assert mounts["/var/cache/niuu"]["subPath"] == "tmp/cache"
