---
name: deploy-noatun
description: Build, push, and deploy to the noatun cluster
---

# Deploy to Noatun

## Prerequisites

- Docker logged into ghcr.io (`~/.docker/config.json`)
- Kubeconfigs at `~/.kube/kubeconfigs/noatun.yaml` (cluster) and `~/.kube/kubeconfigs/local.yaml` (Fleet)
- Infrastructure repo at `/Users/jozefvaneenbergen/git/niuu/infrastructure/infrastructure`
- Helm CLI installed
- On the correct git branch (fetch + verify HEAD before building)

## Step 1: Build Containers (linux/amd64)

ALWAYS fetch and verify you're on the latest remote HEAD first:

```bash
cd /Users/jozefvaneenbergen/git/niuu/software/volundr
git fetch niuulabs feat/ting
git checkout feat/ting
git log --oneline -1  # verify SHA
```

Build all 4 containers (run in parallel):

```bash
docker buildx build --platform linux/amd64 -t ghcr.io/niuulabs/ting:ting -f containers/ting/Dockerfile --push .
docker buildx build --platform linux/amd64 -t ghcr.io/niuulabs/volundr:ting -f containers/volundr/Dockerfile --push .
docker buildx build --platform linux/amd64 -t ghcr.io/niuulabs/skuld:ting -f containers/skuld/Dockerfile --push .
docker buildx build --platform linux/amd64 -t ghcr.io/niuulabs/volundr-web:ting -f containers/volundr-web/Dockerfile --push .
```

Image tag is `ting` (matches what noatun deploys).

## Step 2: Package and Push Helm Charts

Set version (use ting.{latest_PR_number} convention):

```bash
VERSION="0.0.0-ting.XXX"
for chart in skuld volundr ting niuu; do
  sed -i '' "s/^version:.*/version: $VERSION/" charts/$chart/Chart.yaml
  sed -i '' "s/^appVersion:.*/appVersion: \"$VERSION\"/" charts/$chart/Chart.yaml
done
```

Package and push:

```bash
helm dependency update charts/niuu
for chart in skuld volundr ting niuu; do
  helm package charts/$chart -d .helm-packages/
  helm push .helm-packages/${chart}-${VERSION}.tgz oci://ghcr.io/niuulabs/charts
done
```

## Step 3: Update Infrastructure Repo

Two files need updating:

```bash
cd /Users/jozefvaneenbergen/git/niuu/infrastructure/infrastructure

# 1. Fleet chart version
# noatun/niuu-app/helm/fleet.yaml → version: "0.0.0-ting.XXX"

# 2. Values skuld chart references (4 occurrences)
# noatun/niuu-app/helm/values-niuu.yaml → all "0.0.0-ting.YYY" → "0.0.0-ting.XXX"
```

Commit and push:

```bash
git add noatun/niuu-app/helm/
git commit -m "chore(noatun): bump niuu chart to $VERSION"
git push
```

## Step 4: Wait for Fleet Reconciliation

Fleet picks up the push automatically. To force immediate sync:

```bash
KUBECONFIG=~/.kube/kubeconfigs/local.yaml kubectl patch gitrepo -n fleet-default noatun --type=merge -p '{"spec":{"forceSyncGeneration":N}}'
```

(Increment N each time)

## Step 5: Verify Deployment

```bash
KUBECONFIG=~/.kube/kubeconfigs/noatun.yaml kubectl get pods -n volundr
KUBECONFIG=~/.kube/kubeconfigs/noatun.yaml kubectl logs -n volundr -l app.kubernetes.io/name=ting -c ting --tail=20
```

## Fixing Dirty Migrations

If pods crash with "Dirty database version N":

```bash
KUBECONFIG=~/.kube/kubeconfigs/noatun.yaml kubectl exec -n volundr volundr-postgres-1 -- \
  psql -U postgres -d ting -c "UPDATE schema_migrations SET dirty = false WHERE version = N;"
```

Fix table ownership if needed:

```bash
KUBECONFIG=~/.kube/kubeconfigs/noatun.yaml kubectl exec -n volundr volundr-postgres-1 -- \
  psql -U postgres -d ting -c "DO \$\$ DECLARE r RECORD; BEGIN FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tableowner != 'volundr' LOOP EXECUTE 'ALTER TABLE public.' || quote_ident(r.tablename) || ' OWNER TO volundr'; END LOOP; END \$\$;"
```

Then delete the crashing pod to restart.

## Talking to Sessions

Use the PAT stored at `/tmp/volundr_pat.txt` (expires 2027-03-25). If missing, create a new one:

```bash
# 1. Device flow
curl -sk -X POST https://keycloak.niuu.world/realms/volundr/protocol/openid-connect/auth/device \
  -d client_id=volundr-cli -d scope=openid
# 2. User approves URL
# 3. Exchange
curl -sk -X POST https://keycloak.niuu.world/realms/volundr/protocol/openid-connect/token \
  -d grant_type=urn:ietf:params:oauth:grant-type:device_code \
  -d client_id=volundr-cli -d device_code=CODE
# 4. Create PAT
curl -sk -X POST "https://volundr.noatun.asgard.niuu.world/api/v1/users/tokens" \
  -H "Authorization: Bearer $JWT" -d '{"name":"claude-code-cli"}'
```

Send messages to sessions via WebSocket:

```python
python3 << 'PYEOF'
import asyncio, json, ssl
from websockets.asyncio.client import connect

async def send(sid, msg):
    token = open("/tmp/volundr_pat.txt").read().strip()
    uri = f"wss://sessions.noatun.asgard.niuu.world/s/{sid}/session?access_token={token}"
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    async with connect(uri, ssl=ssl_ctx) as ws:
        try:
            while True:
                await asyncio.wait_for(ws.recv(), timeout=2)
        except:
            pass
        await ws.send(json.dumps({"type": "user", "content": msg}))
        print(f"Sent to {sid}")

asyncio.run(send("SESSION_ID_HERE", "YOUR MESSAGE"))
PYEOF
```

## Checking Session Status

```bash
# Find session IDs from pods
KUBECONFIG=~/.kube/kubeconfigs/noatun.yaml kubectl get pods -n skuld

# Check conversation state
KUBECONFIG=~/.kube/kubeconfigs/noatun.yaml kubectl exec -n skuld PODNAME -c skuld -- \
  curl -s http://localhost:8080/api/conversation/history | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(f'turns={len(d.get(\"turns\",[]))}, active={d.get(\"is_active\")}')
"

# Check git state
KUBECONFIG=~/.kube/kubeconfigs/noatun.yaml kubectl exec -n skuld PODNAME -c skuld -- \
  sh -c 'cd /volundr/sessions/*/workspace && git log --oneline -3'
```

## Git Remote

The remote is `niuulabs` not `origin`:

```bash
git push niuulabs feat/ting
```

## Running Tests Locally

Always through Docker (never directly on host):

```bash
docker run --rm -v "$(pwd):/app" -w /app ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
  uv run --extra dev pytest tests/test_ting/ -x -q --no-cov
```
