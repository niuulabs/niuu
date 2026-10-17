# NIU-295 implementation handoff

## Change

Extended `tests/test_adapters/test_harvester.py` with test-only coverage of:

- rejecting unsafe/incomplete Harvester adapter configuration;
- refusing outbound calls that lack an authorization header;
- failing create before resource allocation when image storage is unavailable
  or the configured network is absent;
- reporting deleting and failed VM states; and
- refusing unsafe deletion when an API resource has no UID.

The test fake now exposes only the image-storage and network-presence controls
needed to drive those public adapter outcomes. Production code is unchanged.

## Verification

```text
uv run --extra dev pytest tests/test_adapters/test_harvester.py --cov=volundr.adapters.outbound.harvester --cov-report=term-missing
33 passed; Harvester adapter coverage: 93%

uv run --extra dev ruff check tests/test_adapters/test_harvester.py
All checks passed
```

Repository-wide gate attempt:

```text
make test
/bin/bash: make: command not found

uv run --extra dev --extra k8s pytest tests/ -v --tb=short --cov=src --cov-report=term-missing --cov-fail-under=85
Stopped after two pre-existing failures in tests/integration/test_http_persona_adapter.py
(`test_round_trip_load` and `test_round_trip_list_names`): its test server returns
404 for /api/v1/tokens/workload/exchange. At interruption: 689 passed, 2 failed,
9 skipped, 230 deselected. These failures are outside HarvesterAdapter and this
change did not alter their code.
```

## Delivery

- Branch: `test/niu-295-harvester-adapter` (cut from `regin`)
- Target: `regin`
- Hosted PR: https://github.com/niuulabs/niuu/pull/1140 (base: `regin`)
