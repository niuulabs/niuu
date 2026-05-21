# Three-Mac Ting + Volundr Lab

This guide sets up:

- **one central Ting** on your Mac mini
- **three separate Volundr stacks**
  - Mac mini
  - MacBook
  - second Mac mini or second Mac
- **one Ting UI** that can dispatch to any of those Volundr instances

This is the shortest path to the lab topology you described:

```text
Ting UI/API (Mac mini)
  ├─ Volundr connection: mac-mini
  ├─ Volundr connection: macbook
  └─ Volundr connection: mac-mini-2
```

## Important dev-mode setting

Anonymous local Ting used to force a single local Volundr adapter.

For this lab, set:

```yaml
auth:
  allow_anonymous_dev: true
  default_user_id: dev-user

volundr:
  use_connection_factory_in_dev: true
```

That keeps the local no-auth UI flow, but allows Ting to resolve multiple
stored `CODE_FORGE` connections for the fallback dev user.

## Recommended topology

### Volundr nodes

Run one independent Volundr stack on each machine in local/mini mode.

Each node should have:

- its own config file
- its own embedded or local PostgreSQL state
- its own PAT for Ting
- a stable LAN base URL reachable from the central Ting host

Examples:

- `http://mac-mini.local:8080`
- `http://macbook-pro.local:8080`
- `http://office-mini.local:8080`

### Central Ting node

Run Ting only on the Mac mini and point it at all three Volundr URLs through
integration connections.

## Step 1: Bring up each Volundr

Use the normal local quickstart on each machine.

Minimum goals per node:

- the UI loads
- session creation works locally
- the machine is reachable from the Mac mini over the LAN

Before moving on, verify each Volundr responds from the Mac mini:

```bash
curl -sf http://mac-mini.local:8080/api/v1/identity/me
curl -sf http://macbook-pro.local:8080/api/v1/identity/me
curl -sf http://office-mini.local:8080/api/v1/identity/me
```

If you want deeper checks:

```bash
curl -sf http://mac-mini.local:8080/api/v1/forge/sessions
curl -sf http://macbook-pro.local:8080/api/v1/forge/resources
curl -sf http://office-mini.local:8080/api/v1/forge/sessions
```

## Step 2: Create a PAT on each Volundr

On each Volundr instance:

1. Open **Volundr → Settings → Access Tokens**
2. Create a token with a descriptive name such as `ting-multi-cluster`
3. Copy the token immediately

You need one PAT per Volundr base URL.

### Optional yolo mode

If all Volundr nodes are running in permissive local/dev auth mode and you want
the easiest possible setup, you can skip PATs entirely.

In that case:

- keep `auth.allow_anonymous_dev: true` in Ting
- keep `volundr.use_connection_factory_in_dev: true`
- create the `code_forge` connections with an empty credential value

This is intentionally insecure and should only be used on a trusted local
network for onboarding or quick experiments.

## Step 3: Configure the central Ting

Use a `ting.yaml` like this on the Mac mini:

```yaml
database:
  host: localhost
  port: 5432
  user: ting
  password: ting
  name: ting

volundr:
  url: http://mac-mini.local:8080
  use_connection_factory_in_dev: true
  trusted_connection_test_urls:
    - http://mac-mini.local:8080
    - http://macbook-pro.local:8080
    - http://office-mini.local:8080

auth:
  allow_anonymous_dev: true
  default_user_id: dev-user

credential_store:
  adapter: niuu.adapters.memory_credential_store.MemoryCredentialStore
```

Notes:

- `volundr.url` still needs a value, but multi-cluster dispatch will come from
  stored connections once `use_connection_factory_in_dev: true` is enabled.
- `trusted_connection_test_urls` must include every Volundr URL you want Ting to
  test during integration setup.
- `default_user_id` is the owner Ting will use for your dev-mode integrations.

## Step 4: Add the three Volundr connections in Ting

From the Ting UI on the Mac mini:

1. Open **Ting → Settings → Integrations**
2. Add one `code_forge` connection per Volundr instance
3. Use a distinct name for each node

Suggested names:

- `mac-mini`
- `macbook-pro`
- `office-mini`

For each connection:

- `integration_type`: `code_forge`
- `adapter`: `ting.adapters.volundr_http.VolundrHTTPAdapter`
- `credential_value`: the PAT for that Volundr
- `config.url`: that Volundr's base URL
- `config.name`: the human-readable cluster label

## Step 5: Verify Ting sees all clusters

From the Mac mini:

```bash
curl -sf http://localhost:8000/api/v1/ting/dispatch/clusters | jq
```

Expected shape:

```json
[
  {
    "connection_id": "...",
    "name": "mac-mini",
    "url": "http://mac-mini.local:8080",
    "enabled": true
  }
]
```

You should see all three entries.

## Step 6: Verify session aggregation

The Ting sessions API now aggregates across all configured Volundr adapters.

Launch or keep one session running on a non-primary Volundr, then verify:

```bash
curl -sf http://localhost:8000/api/v1/ting/sessions | jq
```

Look for:

- `session_id`
- `cluster_name`

You should see sessions from whichever Volundr instance they are running on.

## Step 7: Verify targeted dispatch from one UI

In the Ting dispatch screen:

1. select one or more ready runs
2. choose a **Dispatch target**
3. dispatch

Expected behavior:

- the pending bar shows the chosen target
- the success toast names the cluster Ting dispatched to
- the resulting session appears through Ting with the right `cluster_name`

## Smoke-test matrix

Run these three checks before trusting the setup:

1. Dispatch one run to `mac-mini`
2. Dispatch one run to `macbook-pro`
3. Dispatch one run to `office-mini`

Then verify:

1. `GET /api/v1/ting/sessions` shows all three sessions
2. each returned session has the expected `cluster_name`
3. approving a session on a non-primary cluster works from Ting

## Known limits

- Ting still does **manual target selection**, not automatic balancing
- Volundr UI is still **single-backend**, not federated across the three nodes
- if you later disable anonymous dev mode, you will need real trusted auth headers
  or an auth proxy in front of Ting

## Recommended naming convention

Use stable connection names that match the physical machine:

```text
mac-mini
macbook-pro
office-mini
```

That keeps the dispatch target selector and session labels easy to read.
