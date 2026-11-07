"""skuld.session_secrets: the one boot step that turns delivered secrets into a session."""

import json
import os
import subprocess
import sys

import pytest

from skuld import session_secrets
from skuld.session_secrets import SessionSecretsError, exports


def _deliver(root, *, env=None, files=None, credentials=None):
    user = root / "user"
    user.mkdir(parents=True)
    for name, content in (credentials or {}).items():
        (user / name).write_text(json.dumps(content))
    (root / "manifest.json").write_text(json.dumps({"env": env or {}, "files": files or {}}))
    return user


def test_exports_quoted_values_and_links_files(tmp_path):
    secrets = tmp_path / "secrets"
    user = _deliver(
        secrets,
        env={"API_KEY": {"file": "anthropic", "key": "token"}, "MISSING": {"file": "absent"}},
        files={str(tmp_path / "home/.config/gh/hosts.yml"): {"file": "github"}},
        credentials={"anthropic": {"token": "it's $secret"}, "github": {"x": 1}},
    )
    lines = exports({"NIUU_SECRETS_DIR": str(secrets)})
    assert lines == ["export API_KEY='it'\"'\"'s $secret'"]
    link = tmp_path / "home/.config/gh/hosts.yml"
    assert os.readlink(link) == str(user / "github")
    # A second boot finds its own link and leaves it.
    assert exports({"NIUU_SECRETS_DIR": str(secrets)}) == lines


def test_no_manifest_is_no_environment(tmp_path):
    assert exports({"NIUU_SECRETS_DIR": str(tmp_path)}) == []


def test_refuses_a_name_that_is_not_a_variable(tmp_path):
    secrets = tmp_path / "secrets"
    _deliver(secrets, env={"A;rm -rf": {"file": "x"}}, credentials={"x": {}})
    with pytest.raises(SessionSecretsError, match="not an environment variable"):
        exports({"NIUU_SECRETS_DIR": str(secrets)})


def test_places_move_targets_and_refuse_others(tmp_path):
    secrets = tmp_path / "secrets"
    user = _deliver(
        secrets,
        files={"/home/skuld/.netrc": {"file": "netrc"}},
        credentials={"netrc": {}},
    )
    home = tmp_path / "slot-home"
    places = json.dumps({"/home/skuld": str(home), "/workspace": str(tmp_path / "ws")})
    exports({"NIUU_SECRETS_DIR": str(secrets), "NIUU_SESSION_PLACES": places})
    assert os.readlink(home / ".netrc") == str(user / "netrc")

    other = tmp_path / "other"
    _deliver(other, files={"/etc/secret": {"file": "netrc"}}, credentials={"netrc": {}})
    with pytest.raises(SessionSecretsError, match="outside the session home"):
        exports({"NIUU_SECRETS_DIR": str(other), "NIUU_SESSION_PLACES": places})


def test_links_and_refuses_what_is_in_the_way(tmp_path):
    source = tmp_path / "token"
    source.write_text("t")
    target = tmp_path / "var/run/secrets/token"
    links = json.dumps([{"target": str(target), "source": str(source)}])
    environ = {"NIUU_SECRETS_DIR": str(tmp_path / "none"), "NIUU_SESSION_LINKS": links}
    exports(environ)
    assert os.readlink(target) == str(source)
    exports(environ)  # idempotent
    target.unlink()
    target.write_text("someone else's")
    with pytest.raises(SessionSecretsError, match="in the way"):
        exports(environ)


def test_main_prints_exports_and_fails_loudly(tmp_path, monkeypatch, capsys):
    secrets = tmp_path / "secrets"
    _deliver(secrets, env={"K": {"file": "c", "key": "v"}}, credentials={"c": {"v": "1"}})
    monkeypatch.setenv("NIUU_SECRETS_DIR", str(secrets))
    monkeypatch.delenv("NIUU_SESSION_PLACES", raising=False)
    monkeypatch.delenv("NIUU_SESSION_LINKS", raising=False)
    assert session_secrets.main() == 0
    assert capsys.readouterr().out.strip() == "export K=1"
    (secrets / "manifest.json").write_text("{not json")
    assert session_secrets.main() == 1
    assert "could not be delivered" in capsys.readouterr().err


def test_runs_as_a_module_the_boot_evals(tmp_path):
    secrets = tmp_path / "secrets"
    _deliver(secrets, env={"K": {"file": "c", "key": "v"}}, credentials={"c": {"v": "a b"}})
    script = 'exports=$("$PY" -m skuld.session_secrets) && eval "$exports" && printf %s "$K"'
    result = subprocess.run(
        ["sh", "-c", script],
        env={**os.environ, "PY": sys.executable, "NIUU_SECRETS_DIR": str(secrets)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "a b"
