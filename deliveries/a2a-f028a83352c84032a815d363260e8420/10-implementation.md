# NIU-295 implementation handoff

## Change

Added focused Harvester adapter contract tests covering:

- invalid base URL, timeout, page-size, and profile configuration;
- missing authorization headers before any outbound request;
- unavailable image storage and a missing configured network before resources are created;
- deleting and failure VM states; and
- refusal to delete a resource without a UID.

The Harvester HTTP fake gained only the image-storage and network-presence controls necessary to express those outcomes. Production code is unchanged.

## Verification

Focused tests and lint passed:

```text
uv run --extra dev pytest tests/test_adapters/test_harvester.py --cov=volundr.adapters.outbound.harvester --cov-report=term-missing
33 passed in 0.61s
Harvester adapter coverage: 93%
```

```text
uv run ruff check tests/test_adapters/test_harvester.py
All checks passed
```

The repository-wide gate was attempted with its Makefile command, but this environment has no `make` executable:

```text
make test
/bin/bash: line 1: make: command not found
```

The equivalent pytest gate, with the required Kubernetes extra, ran to completion but is red in unrelated persona/platform tests:

```text
uv run --extra k8s pytest tests/ -v --tb=short --cov=src --cov-report=term-missing --cov-fail-under=85
= 265 failed, 26433 passed, 187 skipped, 230 deselected, 1 xfailed, 109 warnings in 1468.80s (0:24:28) =
```

Two exact failures observed at the beginning of that run were reproduced separately without changing persona-adapter code:

```text
uv run --extra k8s pytest tests/integration/test_http_persona_adapter.py::test_round_trip_load tests/integration/test_http_persona_adapter.py::test_round_trip_list_names -vv --tb=short
FAILED tests/integration/test_http_persona_adapter.py::test_round_trip_load - assert None is not None
WARNING  ravn.adapters.personas.http:http.py:209 HttpPersonaAdapter: request failed for 'integ-coder': Client error '404 Not Found' for url 'http://testserver/api/v1/tokens/workload/exchange'
FAILED tests/integration/test_http_persona_adapter.py::test_round_trip_list_names - AssertionError: assert 'alpha-agent' in []
WARNING  ravn.adapters.personas.http:http.py:241 HttpPersonaAdapter: list_names request failed: Client error '404 Not Found' for url 'http://testserver/api/v1/tokens/workload/exchange'
```

The same full-gate run also reports missing mocked workload-token exchanges in `tests/test_ravn/test_personas/test_http.py` and `tests/test_ravn/test_platform_tools.py`, plus `FileNotFoundError: [Errno 2] No such file or directory: 'helm'` in workload-identity chart tests. `NIUU_WORKLOAD_IDENTITY_TOKEN_FILE` was unset in this session. The tracked exchange implementation exists in `src/niuu/adapters/outbound/http_auth.py`; this delivery does not change it or the persona tests.

## Delivery

- Branch: `test/niu-295-harvester-adapter`
- Target: `regin`
- Existing hosted PR: https://github.com/niuulabs/niuu/pull/1140 (base: `regin`); this branch has been rebased onto the current `regin` head and requires a force-with-lease push.
