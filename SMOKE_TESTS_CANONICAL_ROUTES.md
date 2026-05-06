# Smoke-Test Checklist: Canonical Route Parity

> **RAID NIU-768** — Verify canonical route surfaces behave like legacy counterparts
> for the highest-risk domains before cutover and shim removal.

## How to Run

Each test below is a self-contained `curl` or `httpx` call you can run against a
running instance.  Where a legacy route exists, send the same request to both
the legacy and canonical path and compare status code + response shape.

**Scripted**: run the full suite with:

```bash
python -m tests.smoke_tests_route_parity --base-url http://localhost:8080 --token $TOKEN
```

**Manual**: follow the curl examples below.

**Goal**: a fast confidence pass — not a full regression suite.

---

## 1. Identity (highest risk — every authenticated call depends on it)

| # | Legacy Path | Canonical Path | Method | What to Check |
|---|-------------|----------------|--------|---------------|
| 1.1 | `/api/v1/volundr/me` | `/api/v1/identity/me` | `GET` | 200 + same `sub`, `email`, `roles` fields |
| 1.2 | `/api/v1/volundr/identity` | `/api/v1/identity/tenants` | `GET` | 200 + same tenant list |
| 1.3 | `/api/v1/volundr/identity` | `/api/v1/identity/tenants` | `PUT` | 200/204 + same updated tenant object |

**curl examples**
```bash
# 1.1 — compare /me
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/v1/volundr/me > legacy_me.json
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/v1/identity/me > canonical_me.json
diff -u legacy_me.json canonical_me.json

# 1.2 — compare tenants list
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/v1/volundr/identity > legacy_tenants.json
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/v1/identity/tenants > canonical_tenants.json
diff -u legacy_tenants.json canonical_tenants.json
```

---

## 2. Tracker (high risk — session-to-issue linking)

| # | Legacy Path | Canonical Path | Method | What to Check |
|---|-------------|----------------|--------|---------------|
| 2.1 | `/api/v1/tracker/status` | `/api/v1/tracker/status` | `GET` | 200 + same adapter list |
| 2.2 | `/api/v1/tracker/issues` | `/api/v1/tracker/issues` | `GET` | 200 + same issue list shape |
| 2.3 | `/api/v1/tracker/issues/{id}` | `/api/v1/tracker/issues/{id}` | `GET` | 200 + same issue details |
| 2.4 | `/api/v1/tracker/repo-mappings` | `/api/v1/tracker/repo-mappings` | `GET` | 200 + same mappings |
| 2.5 | `/api/v1/tracker/import` | `/api/v1/tracker/import` | `POST` | 202/200 + same job-id response |
| 2.6 | `/api/v1/tracker/projects` | `/api/v1/tracker/projects` | `GET` | 200 + same project list |

**curl example**
```bash
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/v1/tracker/issues > issues.json
jq '.[].id' issues.json | head -5
```

---

## 3. Integrations (high risk — OAuth flows break easily)

| # | Legacy Path | Canonical Path | Method | What to Check |
|---|-------------|----------------|--------|---------------|
| 3.1 | `/api/v1/volundr/integrations` | `/api/v1/integrations` | `GET` | 200 + same integration list |
| 3.2 | `/api/v1/volundr/integrations/{id}` | `/api/v1/integrations/{id}` | `GET` | 200 + same connection details |
| 3.3 | `/api/v1/volundr/integrations/{id}/test` | `/api/v1/integrations/{id}/test` | `POST` | 200 + same test result |
| 3.4 | `/api/v1/volundr/integrations/{slug}/authorize` | `/api/v1/integrations/oauth/{slug}/authorize` | `GET` | 302/redirect to OAuth provider |

**curl example**
```bash
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/v1/integrations > integrations.json
jq '.[].slug' integrations.json
```

---

## 4. Audit (medium risk — compliance / observability)

| # | Legacy Path | Canonical Path | Method | What to Check |
|---|-------------|----------------|--------|---------------|
| 4.1 | `/api/v1/volundr/audit/events` | `/api/v1/audit/events` | `GET` | 200 + same event list |
| 4.2 | `/api/v1/volundr/audit` | `/api/v1/audit` | `GET` | 200 + same audit summary |

**curl example**
```bash
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/v1/audit/events?limit=10 > audit_events.json
jq '. | length' audit_events.json
```

---

## 5. Forge — Sessions & Chronicles (highest risk — core session lifecycle)

| # | Legacy Path | Canonical Path | Method | What to Check |
|---|-------------|----------------|--------|---------------|
| 5.1 | `/api/v1/volundr/sessions` | `/api/v1/forge/sessions` | `GET` | 200 + same session list |
| 5.2 | `/api/v1/volundr/sessions` | `/api/v1/forge/sessions` | `POST` | 201 + same SessionResponse shape |
| 5.3 | `/api/v1/volundr/sessions/{id}` | `/api/v1/forge/sessions/{id}` | `GET` | 200 + same session object |
| 5.4 | `/api/v1/volundr/sessions/{id}` | `/api/v1/forge/sessions/{id}` | `PUT` | 200 + same updated session |
| 5.5 | `/api/v1/volundr/sessions/{id}` | `/api/v1/forge/sessions/{id}` | `DELETE` | 204/200 + same result |
| 5.6 | `/api/v1/volundr/sessions/{id}/start` | `/api/v1/forge/sessions/{id}/start` | `POST` | 200/202 + same session |
| 5.7 | `/api/v1/volundr/chronicles` | `/api/v1/forge/chronicles` | `GET` | 200 + same chronicle list |
| 5.8 | `/api/v1/volundr/chronicles` | `/api/v1/forge/chronicles` | `POST` | 201 + same chronicle object |
| 5.9 | `/api/v1/volundr/sessions/{id}/timeline` | `/api/v1/forge/chronicles/{id}/timeline` | `GET` | 200 + same timeline events |

**curl example**
```bash
# 5.1 — list sessions on both paths
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/v1/volundr/sessions > legacy_sessions.json
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/v1/forge/sessions > canonical_sessions.json
diff -u legacy_sessions.json canonical_sessions.json

# 5.2 — create a session
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"smoke-test-session","model":"claude-sonnet-4-6"}' \
  $BASE/api/v1/forge/sessions > created.json
jq '.id' created.json
```

---

## 6. Forge — Templates, Catalog & Profiles

| # | Legacy Path | Canonical Path | Method | What to Check |
|---|-------------|----------------|--------|---------------|
| 6.1 | `/api/v1/volundr/templates` | `/api/v1/forge/templates` | `GET` | 200 + same template list |
| 6.2 | `/api/v1/volundr/presets` | `/api/v1/forge/presets` | `GET` | 200 + same preset list |
| 6.3 | `/api/v1/volundr/profiles` | `/api/v1/forge/profiles` | `GET` | 200 + same profile list |
| 6.4 | `/api/v1/volundr/resources` | `/api/v1/forge/resources` | `GET` | 200 + same resource list |
| 6.5 | `/api/v1/volundr/models` | `/api/v1/forge/models` | `GET` | 200 + same model catalog |
| 6.6 | `/api/v1/volundr/stats` | `/api/v1/forge/stats` | `GET` | 200 + same stats |
| 6.7 | `/api/v1/volundr/prompts` | `/api/v1/forge/prompts` | `GET` | 200 + same prompt list |
| 6.8 | `/api/v1/volundr/cluster` | `/api/v1/forge/cluster` | `GET` | 200 + same cluster info |

**curl example**
```bash
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/v1/forge/templates > templates.json
jq '.[].name' templates.json
```

---

## 7. Forge — Git & Repos

| # | Legacy Path | Canonical Path | Method | What to Check |
|---|-------------|----------------|--------|---------------|
| 7.1 | `/api/v1/volundr/repos/branches` | `/api/v1/niuu/repos/branches` | `GET` | 200 + same branch list |
| 7.2 | `/api/v1/volundr/repos/prs` | `/api/v1/forge/repos/prs` | `GET` | 200 + same PR list |

**curl example**
```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/v1/niuu/repos/branches?repo_url=github.com/acme/repo" > branches.json
jq '.[]' branches.json
```

---

## 8. Forge — Workspaces

| # | Legacy Path | Canonical Path | Method | What to Check |
|---|-------------|----------------|--------|---------------|
| 8.1 | `/api/v1/volundr/workspaces` | `/api/v1/forge/workspaces` | `GET` | 200 + same workspace list |
| 8.2 | `/api/v1/volundr/workspaces/{sid}` | `/api/v1/forge/workspaces/{sid}` | `DELETE` | 204/200 + same result |

---

## 9. Tokens / PATs

| # | Legacy Path | Canonical Path | Method | What to Check |
|---|-------------|----------------|--------|---------------|
| 9.1 | `/api/v1/volundr/tokens` | `/api/v1/tokens` | `GET` | 200 + same token list |
| 9.2 | `/api/v1/volundr/tokens` | `/api/v1/tokens` | `POST` | 201 + same token (with mask) |

---

## 10. Credentials / Secrets

| # | Legacy Path | Canonical Path | Method | What to Check |
|---|-------------|----------------|--------|---------------|
| 10.1 | `/api/v1/volundr/credentials` | `/api/v1/credentials/user` | `GET` | 200 + same credential list |
| 10.2 | `/api/v1/volundr/credentials` | `/api/v1/credentials/user` | `POST` | 201 + same credential response |
| 10.3 | `/api/v1/volundr/secrets/store` | `/api/v1/credentials/user` | `GET` | 200 + same store list |

---

## 11. Admin / Settings

| # | Legacy Path | Canonical Path | Method | What to Check |
|---|-------------|----------------|--------|---------------|
| 11.1 | `/api/v1/volundr/admin/settings` | `/api/v1/volundr/admin/settings` | `GET` | 200 + same config (admin only) |
| 11.2 | `/api/v1/volundr/feature-flags` | `/api/v1/features` | `GET` | 200 + same feature catalog |

---

## 12. Cross-Domain: Session → Forge → Timeline

A realistic end-to-end flow exercising the most paths at once:

```bash
# Step 1: Create session via canonical forge route
SESSION=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"e2e-smoke","model":"claude-sonnet-4-6"}' \
  $BASE/api/v1/forge/sessions | jq -r '.id')

# Step 2: List it back via both paths
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/v1/volundr/sessions | jq ".[] | select(.id==\"$SESSION\")" > legacy_check.json
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/v1/forge/sessions | jq ".[] | select(.id==\"$SESSION\")" > canonical_check.json

# Step 3: Verify both have the same fields
diff <(jq 'del(.chat_endpoint, .code_endpoint)' legacy_check.json) \
     <(jq 'del(.chat_endpoint, .code_endpoint)' canonical_check.json)

# Step 4: Archive it
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" \
  $BASE/api/v1/forge/sessions/$SESSION/archive > archive_resp.json

# Step 5: Cleanup
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" \
  $BASE/api/v1/forge/sessions/$SESSION
```

---

## Pass Criteria

| Criteria | Pass |
|----------|------|
| All legacy routes still return 200/201/204 | ✓ |
| Canonical routes return identical status codes | ✓ |
| Response JSON shapes match (ignoring internal-only fields like `pod_name`) | ✓ |
| No new 5xx errors on canonical paths | ✓ |
| Legacy routes still carry deprecation headers (`X-Niuu-Legacy-Route`, `Deprecation`) | ✓ |

---

## Notes

- **Deprecation headers**: Every legacy route should include `Deprecation: true`,
  `X-Niuu-Legacy-Route`, `X-Niuu-Canonical-Route`, `Link`, and `Sunset` headers.
  Check with `curl -I` or inspect response headers.

- **Legacy route hit tracking**: You can inspect legacy route usage at:
  ```
  GET /api/v1/niuu/compat/legacy-routes
  ```
  This returns a JSON array of `{legacyPath, canonicalPath, method, hits}` sorted by
  hit count. Clear with `DELETE /api/v1/niuu/compat/legacy-routes`.

- **Auth**: Replace `$BASE` with your instance URL and `$TOKEN` with a valid bearer
  token. In dev mode without auth, omit the Authorization header.

- **Time budget**: ~15 minutes for a full pass of all 12 domains.

---
