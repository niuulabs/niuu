# Module Boundary Rules

## Ting Must Never Import From Volundr

**Ting (`src/ting/`) is strictly forbidden from importing anything from Volundr (`src/volundr/`).**

If Volundr has modules that Ting also needs, they MUST be extracted into the shared `niuu` module (`src/niuu/`). Both Volundr and Ting then import from `niuu`.

## Import Direction

```
niuu (shared)
  ↑         ↑
  |         |
volundr    ting
```

- `volundr` → can import from `niuu`
- `ting` → can import from `niuu`
- `ting` → CANNOT import from `volundr`
- `volundr` → CANNOT import from `ting`
- `niuu` → CANNOT import from `volundr` or `ting`

## Niuu Module Structure

`src/niuu/` follows the same hexagonal architecture as `src/volundr/` and `src/ting/`:

```
src/niuu/
├── domain/
│   ├── models.py      # Shared domain models
│   └── exceptions.py  # Shared exceptions
├── ports/
│   └── *.py           # Shared port interfaces (abstract base classes)
└── adapters/
    └── *.py           # Shared adapter implementations
```

## When to Extract

Extract to `niuu` when:
- Both Volundr and Ting need the same interface, model, or service
- A new package (future) would also need the same module

Do NOT extract to `niuu` when:
- Only one package uses it — keep it in that package
- It's an implementation detail specific to one package
