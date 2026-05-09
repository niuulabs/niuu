# Smoke-Test Checklist: Canonical Public Routes

This checklist is for the post-cutover world: exercise the canonical route
surface directly and verify the platform is usable without any Volundr-scoped
public API paths.

## How to Run

**Scripted**

```bash
python -m tests.smoke_tests_route_parity --base-url http://localhost:8080 --token "$TOKEN"
```

**Manual**

Set:

```bash
BASE=http://localhost:8080
AUTH=(-H "Authorization: Bearer $TOKEN")
```

## Core Domains

### Identity

- `GET /api/v1/identity/me`
- `GET /api/v1/identity/tenants`
- `GET /api/v1/identity/users`
- `GET /api/v1/identity/auth/config`

Check:

- requests authenticate successfully
- tenant and membership data load without redirects or fallback paths

### Forge

- `GET /api/v1/forge/sessions`
- `POST /api/v1/forge/sessions`
- `GET /api/v1/forge/chronicles`
- `GET /api/v1/forge/templates`
- `GET /api/v1/forge/presets`
- `GET /api/v1/forge/profiles`
- `GET /api/v1/forge/resources`
- `GET /api/v1/forge/models`
- `GET /api/v1/forge/stats`
- `GET /api/v1/forge/workspaces`
- `GET /api/v1/forge/repos/prs`

Check:

- session create/list/archive/delete all work
- catalog endpoints return stable snake_case payloads
- no UI path silently depends on a second namespace

### Credentials

- `GET /api/v1/credentials/types`
- `GET /api/v1/credentials/user`
- `GET /api/v1/credentials/secrets`
- `GET /api/v1/credentials/mcp-servers`

Check:

- secret metadata and MCP server discovery resolve from the credentials domain

### Integrations

- `GET /api/v1/integrations`
- `GET /api/v1/integrations/{id}`
- `POST /api/v1/integrations/{id}/test`
- `GET /api/v1/integrations/oauth/{slug}/authorize`

Check:

- OAuth authorize flow starts from the integrations domain
- connection tests do not depend on any compatibility path

### Tracker

- `GET /api/v1/tracker/status`
- `GET /api/v1/tracker/issues`
- `GET /api/v1/tracker/repo-mappings`

Check:

- issue search and repo mappings resolve from tracker alone

### Features

- `GET /api/v1/features`
- `GET /api/v1/features/modules`
- `PUT /api/v1/features/preferences`
- `POST /api/v1/features/modules/{key}/toggle`

Check:

- feature catalog is no longer coupled to identity or forge routing

### Tokens

- `GET /api/v1/tokens`
- `POST /api/v1/tokens`
- `DELETE /api/v1/tokens/{id}`

Check:

- PAT lifecycle is reachable from the standalone tokens surface

## Quick Manual Flow

```bash
curl -s "${AUTH[@]}" "$BASE/api/v1/identity/me" | jq .
curl -s "${AUTH[@]}" "$BASE/api/v1/forge/sessions" | jq 'length'
curl -s "${AUTH[@]}" "$BASE/api/v1/features/modules" | jq 'length'
curl -s "${AUTH[@]}" "$BASE/api/v1/credentials/mcp-servers" | jq .
curl -s "${AUTH[@]}" "$BASE/api/v1/tracker/status" | jq .
```

## Pass Criteria

- No request in the golden path depends on a Volundr-scoped public URL.
- The browser app loads and functions from canonical routes only.
- Route responses remain stable and snake_case.
- No canonical route returns a `5xx`.

- **Time budget**: ~15 minutes for a full pass of all 12 domains.

---
