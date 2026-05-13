# Code Style

## Python

- **Target**: Python 3.12+
- **Linter/formatter**: ruff
- **Line length**: 100 characters

### Early returns

Always use early returns. No nested conditionals, no single-line else:

```python
# Good
async def process(self, signal: Signal) -> Response | None:
    if not self.is_running:
        return None

    if signal.priority < self.threshold:
        return None

    return await self.handle(signal)
```

### Modern syntax

- `X | None` not `Optional[X]`
- `match` statements where appropriate
- f-strings over `.format()`

### No magic numbers

All timing values, thresholds, and counts come from config with sensible defaults. No hardcoded numbers in business logic.

### Architecture rules

- Domain services import from `ports/` only, never from `adapters/`
- No ORM — raw SQL with asyncpg
- Dynamic adapter pattern for new adapters (class path in YAML config)
- No custom auth/token layers — delegate to OIDC

### Formatting

```bash
uv run ruff check src/ tests/    # Lint
uv run ruff format src/ tests/   # Format
```

## TypeScript (Web UI)

- **Linter**: ESLint
- **Formatter**: Prettier
- **Workspace**: `web-next/`
- **App shell**: `web-next/apps/niuu`
- **Shared packages**: `web-next/packages/*`

```bash
cd web-next
pnpm lint
pnpm format:check
pnpm typecheck
```
