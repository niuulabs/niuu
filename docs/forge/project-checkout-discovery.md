# Connect a project from a host folder

Project creation no longer requires the client to invent a UUID or copy a Git URL.
The client chooses one host and submits an absolute path to its existing checkout.

```
POST /api/v1/forge/projects/discover
{"workspace_path":"/home/horde/projects/kit"}

POST /api/v1/forge/projects/connect
{"workspace_path":"/home/horde/projects/kit","name":"Kit"}
```

Discovery returns a validated ForgeProject without database writes. Connect repeats
discovery, then uses the existing durable registration service. The optional name
applies on first registration; reconnecting an existing registration is not a
rename operation. Existing explicit registration and project/session APIs remain
compatible. No new database migration is required beyond Projects migration 066.

The Git workspace adapter executes `git -C <folder> remote -v` without a shell.
`origin` wins; without it there must be one unique fetch remote. SSH `git@` and
HTTPS remotes normalize to a credential-free HTTPS identity. Credential-bearing,
missing, or ambiguous remotes fail with actionable errors. Timeouts, metadata
budgets, workspace allowlists, and context validation apply before registration.

An existing project.json UUID is retained, with its declared remote checked against
the checkout. Without a manifest, UUIDv5 of the canonical remote makes repeated
requests and cross-host clones stable. No files are created, edited, staged, or
committed by discovery. A host cannot silently reconnect the same identity from a
different local folder or access another principal's project.

The mesh adapter forwards discovery/connect to exactly the selected instance and
retains remote errors. It never selects another host because discovery failed.

Validation: 38 project tests, 58 existing mesh API regressions, four isolated real
PostgreSQL tests, warnings as errors; 85.92% coverage across the affected project
domain/service, native REST router, and Git workspace adapter. Real Git tests cover
remote preference, normalization, worktrees, manifest identity, retries, invalid
metadata, path boundaries, timeouts, and cancellation cleanup.

The Lexi iOS companion is on `dev/project-polish` (build 2258). Thor and Spark run
the discovery implementation at `44f85dc3`. build-bro still requires a Projects
server upgrade before Kit can be connected; its initial revision `f44f62d5` had no
Projects routes. Deployment evidence and rollback files are kept under
`/home/thor/.niuu/validation/project-polish-20260912/`.
