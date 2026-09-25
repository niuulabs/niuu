# Commit Rules

## Conventional Commits

All commits MUST follow the [Conventional Commits](https://www.conventionalcommits.org/) specification.

### Format

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Types

| Type | Description |
|------|-------------|
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation only changes |
| `style` | Changes that don't affect code meaning (whitespace, formatting) |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `perf` | Code change that improves performance |
| `test` | Adding missing tests or correcting existing tests |
| `build` | Changes to build system or external dependencies |
| `ci` | Changes to CI configuration files and scripts |
| `chore` | Other changes that don't modify src or test files |

### Scopes

Use the package or area name as scope:

- `volundr`, `skuld`, `ravn`, `ting`, `niuu`, `bifrost`, `mimir`, `sleipnir`,
  `observatory`, `guild`, `identity`, `cli` - Package changes
- `web` (or the plugin, e.g. `plugin-ting`) - `web-next/` changes
- `charts`, `ci`, `deps` - Deployment, CI and dependency changes
- Omit the scope for repo-wide changes (`docs: …`, `chore: …`)

### Examples

```
feat(ravn): commission a tool build when a capability is missing

fix(skuld): stop swallowing Sleipnir mesh delivery failures

refactor(ting): remove the legacy run-confidence scoring pipeline

test(volundr): cover the session read-state migration

docs: update README with installation instructions

chore: update dependencies
```

### Rules

- Use imperative mood: "add" not "added" or "adds"
- Don't capitalize first letter of description
- No period at the end of description
- Keep description under 72 characters
- Use body for detailed explanation if needed
