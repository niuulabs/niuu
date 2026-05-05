# Smoke Tests — Canonical Route Parity (NIU-768)

Scripted and manual smoke tests to verify that canonical route surfaces
behave like their legacy counterparts for the highest-risk domains.

## Purpose

Fast confidence pass for cutover and shim removal. NOT a full test suite
replacement — just a quick sanity check across identity, tracker,
integrations, audit, and forge domains.

## Automated Testing

Run the automated parity check against a running instance:

```bash
# Default (localhost:8080, no auth)
python -m tests.smoke_tests_route_parity

# With authentication
python -m tests.smoke_tests_route_parity \
  --base-url http://localhost:8080 \
  --token "$TOKEN"

# Filter to a single domain for focused testing
python -m tests.smoke_tests_route_parity --domain forge

# With timeout
python -m tests.smoke_tests_route_parity --timeout 30.0
```

### Domains

| Domain       | Route Pairs | Priority        |
|--------------|-------------|-----------------|
| identity     | 4           | HIGH (me, tenants) |
| tracker      | 3           | HIGH (status, mappings, issues) |
| integrations | 1           | HIGH            |
| audit        | 1           | HIGH            |
| forge        | 16          | HIGH (sessions, chronicles, templates) |
| tokens       | 2           | HIGH            |
| credentials  | 3           | HIGH            |
| features     | 1           | MEDIUM          |

## Manual Checklist

Print a numbered checklist for manual execution:

```bash
# Template only (no server needed)
python -m tests.smoke_tests_route_parity --checklist

# Template with live results appended
python -m tests.smoke_tests_route_parity \
  --base-url http://localhost:8080 \
  --checklist
```

### How to Use

1. Start volundr/niuu with both legacy and canonical routes active
2. For each item, verify both routes return equivalent responses
3. Mark `[x]` when passed, `[ ]` when skipped or failing
4. Sign off and date the checklist

## Route Pairs

The test compares these legacy → canonical route pairs:

```
/api/v1/volundr/me            → /api/v1/identity/me
/api/v1/volundr/tenants       → /api/v1/identity/tenants
/api/v1/volundr/settings      → /api/v1/identity/settings
/api/v1/volundr/users         → /api/v1/identity/users

/api/v1/volundr/tracker/status      → /api/v1/tracker/status
/api/v1/volundr/tracker/mappings    → /api/v1/tracker/repo-mappings
/api/v1/volundr/tracker/issues      → /api/v1/tracker/issues

/api/v1/volundr/integrations        → /api/v1/integrations

/api/v1/audit/events                → /api/v1/audit/events

/api/v1/volundr/sessions            → /api/v1/forge/sessions
/api/v1/volundr/chronicles          → /api/v1/forge/chronicles
/api/v1/volundr/events              → /api/v1/forge/events
/api/v1/volundr/templates           → /api/v1/forge/templates
/api/v1/volundr/presets             → /api/v1/forge/presets
/api/v1/volundr/profiles            → /api/v1/forge/profiles
/api/v1/volundr/resources           → /api/v1/forge/resources
/api/v1/volundr/prompts             → /api/v1/forge/prompts
/api/v1/volundr/mcp-servers         → /api/v1/forge/mcp-servers
/api/v1/volundr/models              → /api/v1/forge/models
/api/v1/volundr/stats               → /api/v1/forge/stats
/api/v1/volundr/cluster             → /api/v1/forge/cluster
/api/v1/volundr/repos/branches      → /api/v1/niuu/repos/branches
/api/v1/volundr/repos/prs           → /api/v1/forge/repos/prs
/api/v1/volundr/git                 → /api/v1/forge/git
/api/v1/volundr/workspaces          → /api/v1/forge/workspaces

/api/v1/volundr/tokens              → /api/v1/tokens
/api/v1/users/tokens                → /api/v1/tokens

/api/v1/volundr/credentials         → /api/v1/credentials
/api/v1/volundr/secrets             → /api/v1/credentials/secrets
/api/v1/volundr/mcp-servers         → /api/v1/credentials/mcp-servers

/api/v1/volundr/features            → /api/v1/features
```

## Notes

- Internal-only fields (`chat_endpoint`, `code_endpoint`, `pod_name`, etc.)
  are stripped before comparison
- 204 No Content responses are matched without body comparison
- Both routes must return the same HTTP status code
- Response JSON shapes must match after stripping internal fields
