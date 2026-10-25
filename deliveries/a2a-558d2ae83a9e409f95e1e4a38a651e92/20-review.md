# NIU-1151 — review

## Verdict

`needs_changes`

## Finding

### Major — byte limit is not enforced for UTF-8 ConfigMap values

- `src/ravn/adapters/tools/kubernetes.py:392` decodes an arbitrary byte suffix
  with `errors="replace"`.  If `tail_bytes` starts in the middle of a UTF-8
  code point, the replacement character encodes to three bytes, so the returned
  payload exceeds the configured/requested byte limit and is not an intact
  suffix.  For example, a value ending in `€` requested with `tail_bytes=1`
  returns `�` (three UTF-8 bytes).  Trim the leading partial UTF-8 code point
  before decoding (or otherwise select only complete code points within the
  limit), and add a regression asserting the returned UTF-8 byte length never
  exceeds `tail_bytes`/the configured cap.

## Verification

- Reviewed committed repo-root diff `01af0978..087db8e5` on branch `regin`;
  the target is `origin/regin` at `087db8e5` before this review artifact.
- Ran `uv run pytest -q tests/test_ravn/test_kubernetes_inspect_tool.py
  tests/ravn/unit/test_builtin_registry.py tests/test_ravn/test_config.py`:
  **224 passed**.
- Ran `uv run ruff check src/ravn/adapters/tools/kubernetes.py
  src/ravn/config.py src/ravn/adapters/tools/builtin_registry.py
  tests/test_ravn/test_kubernetes_inspect_tool.py
  tests/ravn/unit/test_builtin_registry.py`: **All checks passed**.
- Reproduced the reported bound failure with a `config.yaml` ending in `€` and
  `tail_bytes=1`: the response value encoded to three bytes.  Live cluster
  verification remains unavailable because neither `kubectl` nor Teleport is
  installed in this workspace.

## Recommendation

Fix the UTF-8 boundary handling and add the bounded-byte regression test, then
request re-review.
