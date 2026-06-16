# Canonical Route Smoke Tests

This repo now treats the canonical public namespaces as the source of truth.
The smoke-test helper at `tests/smoke_tests_route_parity.py` exercises the
main route surface directly instead of comparing against retired Volundr-scoped
aliases.

## Checklist

1. Start the stack with the canonical API mounts enabled.
2. Run the smoke-test helper or use `--checklist` for a manual pass.
3. Verify the priority routes respond without 5xx errors.
4. Record the date and the environment used for the check.

## Canonical Route Surface

```text
/api/v1/identity/me
/api/v1/identity/tenants
/api/v1/identity/settings
/api/v1/identity/users

/api/v1/tracker/status
/api/v1/tracker/repo-mappings
/api/v1/tracker/issues

/api/v1/integrations

/api/v1/audit/events

/api/v1/forge/sessions
/api/v1/forge/chronicles
/api/v1/forge/stats
/api/v1/forge/cluster/resources

/api/v1/volundr/launch-specs
/api/v1/volundr/session-definitions
/api/v1/volundr/resources
/api/v1/volundr/prompts

/api/v1/niuu/repos/branches

/api/v1/tokens

/api/v1/credentials/types
/api/v1/credentials/user
/api/v1/credentials/secrets
/api/v1/credentials/mcp-servers

/api/v1/features
```

## Notes

- The smoke test is intentionally lightweight and is aimed at route-surface sanity checks.
- Authenticated routes need a valid bearer token when run against a secured environment.
- A successful run should not produce any 5xx responses.
