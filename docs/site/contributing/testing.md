# Testing

## Coverage requirements

Both backend and web UI enforce **85% minimum coverage**.

## Backend

```bash
# Run all tests with coverage
uv run pytest tests/ -v

# Run specific test
uv run pytest -k "test_create_session" -v

# Verbose with short tracebacks
uv run pytest tests/ -v --tb=short
```

### Rules

- Use `pytest` with `pytest-asyncio`
- Zero warnings — all pytest warnings must be resolved
- Test against ports (interfaces), not adapters
- Mock infrastructure in tests
- Use fixtures for common setup
- No Docker for database tests — use mocking/patching

### Test structure

```
tests/
├── conftest.py           # Shared fixtures
├── test_services/        # Domain service tests
├── test_adapters/        # Adapter unit tests
└── test_routes/          # REST endpoint tests
```

### Configuration

Coverage is configured in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "--cov=src/volundr --cov-report=term-missing --cov-fail-under=85"

[tool.coverage.run]
source = ["src/volundr"]
branch = true
```

## Web UI

```bash
cd web-next
pnpm install --frozen-lockfile

# Single run with coverage
pnpm test

# Playwright end-to-end coverage
pnpm test:e2e
```

### Rules

- Use `vitest` with `@testing-library/react`
- Co-locate test files next to source (`Component.test.tsx`)
- Mock service ports in component tests
- Coverage thresholds: 85% on statements, branches, functions, lines

## CI

The CI pipeline runs frontend checks against `web-next/`:

1. **Lint** — ruff check + format check
2. **Test** — pytest with coverage, uploaded to Codecov
3. **web-next Lint** — ESLint + Prettier + TypeScript check
4. **web-next Test** — vitest with coverage, uploaded to Codecov
5. **Helm Lint** — lint both Helm charts
