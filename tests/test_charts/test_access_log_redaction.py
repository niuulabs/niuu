"""No chart may write a request's query string to an access log.

Browsers cannot set an Authorization header on a WebSocket, so external web
clients open Forge/Ravn sockets with ``?token=<JWT>`` or ``?access_token=``.
Any sidecar that logs the full request target therefore logs live bearer
tokens. Every Envoy access log must log the path without its query, and every
nginx access log must use a format built from ``$uri`` rather than
``$request``/``$request_uri``/``$args``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
CHARTS = REPO_ROOT / "charts"
ENVOY_CHARTS = sorted(p.parents[1].name for p in CHARTS.glob("*/templates/envoy-configmap.yaml"))
NGINX_CHARTS = sorted(p.parents[1].name for p in CHARTS.glob("*/templates/nginx-configmap.yaml"))

# %REQ(:PATH)%, %REQ(X-ENVOY-ORIGINAL-PATH?:PATH)% and %PATH%/%PATH(WQ)% all
# include the query string. %REQ_WITHOUT_QUERY(...)% does not match.
_ENVOY_PATH_WITH_QUERY = re.compile(r"%REQ\([^)]*PATH[^)]*\)%|%PATH(?:\((?!NQ)[^)]*\))?%")
_REQ_WITHOUT_QUERY_FORMATTER = "envoy.formatter.req_without_query"
_NGINX_QUERY_VARIABLES = re.compile(r"\$(request|request_uri|args|query_string|arg_\w+)\b")


def _render(chart: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "test", str(CHARTS / chart), "--set", "envoy.enabled=true"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _configmap_value(chart: str, key: str) -> str:
    return next(
        doc["data"][key]
        for doc in _render(chart)
        if doc.get("kind") == "ConfigMap" and key in (doc.get("data") or {})
    )


def _access_logs(node: object) -> list[dict]:
    found: list[dict] = []
    if isinstance(node, dict):
        found.extend(node.get("access_log") or [])
        for value in node.values():
            found.extend(_access_logs(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_access_logs(item))
    return found


def _format_strings(log_format: dict) -> list[str]:
    strings = [log_format.get("text_format", "")]
    strings.append((log_format.get("text_format_source") or {}).get("inline_string", ""))
    strings.extend(str(v) for v in (log_format.get("json_format") or {}).values())
    return [s for s in strings if s]


@pytest.mark.parametrize("chart", ENVOY_CHARTS)
def test_envoy_access_log_never_includes_the_query_string(chart: str) -> None:
    config = yaml.safe_load(_configmap_value(chart, "envoy.yaml"))
    for access_log in _access_logs(config):
        log_format = access_log["typed_config"].get("log_format")
        assert log_format, f"{chart}: an access log without log_format uses Envoy's default"
        formats = _format_strings(log_format)
        assert formats, f"{chart}: access log format is not a recognised text/json format"
        for fmt in formats:
            assert not _ENVOY_PATH_WITH_QUERY.search(fmt), f"{chart} logs the query: {fmt!r}"
            if "%REQ_WITHOUT_QUERY(" in fmt:
                names = [f["name"] for f in log_format.get("formatters") or []]
                assert _REQ_WITHOUT_QUERY_FORMATTER in names, (
                    f"{chart}: %REQ_WITHOUT_QUERY% needs the {_REQ_WITHOUT_QUERY_FORMATTER} "
                    "formatter registered or Envoy rejects the config"
                )


@pytest.mark.parametrize("chart", NGINX_CHARTS)
def test_nginx_access_log_never_includes_the_query_string(chart: str) -> None:
    conf = _configmap_value(chart, "nginx.conf")
    formats = dict(re.findall(r"log_format\s+(\w+)\s+'([^;]*)';", conf))
    for directive in re.findall(r"^\s*access_log\s+([^;]+);", conf, flags=re.MULTILINE):
        parts = directive.split()
        if parts[0] == "off":
            continue
        # With no named format nginx uses "combined", whose $request has the query.
        assert len(parts) > 1 and parts[1] in formats, f"{chart}: access_log {directive!r}"
        assert not _NGINX_QUERY_VARIABLES.search(formats[parts[1]]), (
            f"{chart} logs the query: {formats[parts[1]]!r}"
        )
