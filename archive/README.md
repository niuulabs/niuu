# Archived catalogue

Bundled Ting workflows and built-in personas that are **not shipped**: not in the
wheel, not in the container images, not in the Nuitka binary, not in the Helm
charts, and not served or seeded by Ting or the persona registry. The files are
kept here byte for byte so they can be restored.

Nothing under `archive/` is read at runtime. The shipped code carries the list of
what is archived (`ARCHIVED_SYSTEM_WORKFLOW_IDS` in `src/ting/system_workflows.py`,
`ARCHIVED_BUILTIN_PERSONAS` in `src/personas/archived.py`), and anything that is
kept and still names an archived item stops at startup. `tests/test_catalog_archive.py`
keeps the lists, the archive and the packaging in step.

## What is here

### `workflows/`

| File | What it was for |
|---|---|
| `code-review-flow.yaml` | Tracker-driven: implement a ticket, iterate through code review, merge. No delivery record. |
| `infra-change-flow.yaml` | Tracker-driven change to a GitOps repository on an isolated branch; merge waits for an approval gate. |
| `tracker-delivery-flow.yaml` | Tracker-driven: implement, review, merge, then publish a durable delivery record. |

### `personas/`

| File | What it was for |
|---|---|
| `postmortem-analyst.yaml` | Reads a finished task's history and writes a memory document for Mímir. |
| `product-steward.yaml` | Chat persona for a resident that sharpens a product idea and keeps its thread in Mímir. |

The three workflows pin `closer`, `coder`, `reviewer` and `publisher`; those
personas are still shipped because the Reviewed Delivery Flow uses them.

## Restore an item

Restoring is a code change, and it has to be complete or the service refuses to
start.

1. Move the file back.
   * Workflow: `git mv archive/workflows/<name>.yaml src/ting/workflows/`
   * Persona: `git mv archive/personas/<name>.yaml src/personas/builtin/`
2. Remove it from the shipped list so nothing treats it as archived.
   * Workflow: delete its id from `ARCHIVED_SYSTEM_WORKFLOW_IDS`
     (`src/ting/system_workflows.py`). It is then no longer in
     `RETIRED_SYSTEM_WORKFLOW_IDS` either.
   * Persona: delete its name from `ARCHIVED_BUILTIN_PERSONAS`
     (`src/personas/archived.py`).
3. Re-pin, because the persona digests a workflow pins may have moved while it
   was archived: `uv run python scripts/repin_bundled_workflows.py`.
4. If the workflow's `version` changes, add the new `id@version` to
   `tests/test_ting/data/bundled_workflow_versions.lock.json`. The lock keeps the
   archived versions as history, and `tests/test_catalog_archive.py` fails if an archived
   file no longer matches its locked revision, so restore before editing.
5. Run `uv run --extra dev pytest tests/test_catalog_archive.py tests/test_ting/test_system_workflows.py tests/test_ting/test_bundled_workflow_versions_lock.py tests/test_personas`.

Migration `migrations/ting/000059_detach_archived_system_workflows.up.sql` cleared
the sagas, project boards and ticket coordinators that named the three archived
workflows. Restoring a workflow does not bring those assignments back; assign it
again where it is needed.

## Archive another item

The reverse: `git mv` the file here, add its id or name to the shipped list,
update every test that counts or names it, and delete nothing else. The
`tests/test_catalog_archive.py` tests fail until the lists, the archive and everything that
still names the item agree.
