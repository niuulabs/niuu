# Branching & PR Rules

## Default PR Target

- **PRs target the branch they were created from** — not a fixed branch
- If you branched from `ting`, the PR targets `ting`
- If you branched from `main`, the PR targets `main`
- When in doubt, check `git log --oneline --graph` to find the parent branch
- **Never target `main` unless the user explicitly requests it** — `main` is for production releases only

## Branch Flow

```
feature-branch ──► parent branch (where you branched from) ──► main (production releases)
```

- Feature branches merge back into the branch they were created from
- `main` is for production releases only
- Long-lived topic branches (e.g. `ting`, `dev`) integrate features before merging to `main`
