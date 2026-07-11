# Testing Rules

## Coverage Requirements

- **Minimum 85% coverage** required for both backend and web
- Do not lower Codecov targets, thresholds, or coverage gates to get a PR through. Raise coverage instead.
- Tests MUST exist and pass before completing work

## Backend Testing

- Use pytest with pytest-asyncio
- **Zero warnings** - all pytest warnings must be resolved
- Coverage is enforced by pytest-cov
- Test against **ports** (interfaces), not adapters
- Mock infrastructure in tests
- Use fixtures for common setup

### Backend Test Structure

```
tests/
├── __init__.py
├── conftest.py          # Shared fixtures
├── test_regions/
├── test_ports/
└── test_adapters/
```

### Backend Commands

```bash
make test              # Run tests with coverage
make verify            # Full lint + test
pytest -v              # Verbose test output
pytest -k "test_name"  # Run specific test
```

## Web UI Testing

- Use vitest with @testing-library/react
- Coverage thresholds: 85% on statements, branches, functions, lines
- Co-locate test files next to source (e.g. `Component.test.tsx`)
- Mock service ports in component tests

### Web Commands

```bash
cd web-next
pnpm test        # Run the coverage-gated unit suite
pnpm test:watch  # Run tests in watch mode
```
