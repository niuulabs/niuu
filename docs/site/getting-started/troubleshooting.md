# Troubleshooting

Common issues when running Volundr locally and how to fix them.

---

## `niuu: command not found`

The `niuu` binary is not in your PATH.

**Fix:**

```bash
# Check if the binary exists
ls -la /usr/local/bin/niuu

# If not, re-download and install it
curl -fsSL https://github.com/niuulabs/volundr/releases/latest/download/niuu-darwin-arm64 -o niuu
chmod +x niuu
sudo mv niuu /usr/local/bin/
```

Make sure `/usr/local/bin` is in your PATH:

```bash
echo $PATH | tr ':' '\n' | grep /usr/local/bin
```

If not, add it to your shell profile (`~/.zshrc`, `~/.bashrc`, etc.):

```bash
export PATH="/usr/local/bin:$PATH"
```

---

## `claude` binary not found

Volundr sessions use the `claude` CLI as the AI agent. If it's not installed, sessions will fail to start.

**Fix:**

Install the Claude CLI:

```bash
# macOS
brew install claude

# Or download directly from https://claude.ai/download
```

Verify it's available:

```bash
claude --version
```

---

## Anthropic API key invalid or missing

**Symptoms:**

- Sessions fail during PROVISIONING
- Error: `authentication_error` or `invalid_api_key`

**Fix:**

1. Check your key is set:

    ```bash
    niuu config get anthropic.api_key
    ```

2. If missing or wrong, re-run the init wizard:

    ```bash
    niuu volundr init
    ```

3. Or set it directly:

    ```bash
    niuu config set anthropic.api_key sk-ant-api03-xxxx...
    ```

Make sure your key starts with `sk-ant-` and has not expired.

---

## Port already in use

**Symptoms:**

- `niuu volundr up` fails with "address already in use"
- Error on port 5432 (PostgreSQL) or 8080 (API server)

**Fix:**

Find what's using the port:

```bash
# macOS
lsof -i :8080

# Linux
ss -tlnp | grep 8080
```

Either stop the conflicting process, or use a different port:

```bash
# For the API server
HOST=127.0.0.1 PORT=9000 niuu volundr up
```

If port 5432 is in use, you likely have another PostgreSQL instance running. Either stop it or switch to external database mode:

```bash
niuu volundr init  # Select "external" database mode and point to your existing PostgreSQL
```

---

## Session stuck in PROVISIONING

**Symptoms:**

- Session status shows `PROVISIONING` for more than 60 seconds
- No error message visible

**Fix:**

1. Check service status:

    ```bash
    niuu volundr status
    ```

    All services should show `running`. If any show errors, restart:

    ```bash
    niuu volundr down
    niuu volundr up
    ```

2. Check the API server logs for errors. Look for messages about failed health checks or database connectivity.

3. If using embedded PostgreSQL, make sure the database started successfully (check for port 5432 in the status output).

4. Try stopping and re-creating the session.

---

## Git clone fails

**Symptoms:**

- Session fails during PROVISIONING with a git error
- Error mentions authentication, permissions, or network issues

### Authentication errors

Make sure your GitHub token has the correct scopes:

- `repo` — full control of private repositories
- `read:org` — if you're accessing organization repos

Re-configure the token:

```bash
niuu volundr init  # Re-enter your GitHub token when prompted
```

### Network errors

If you're behind a corporate proxy:

```bash
export HTTPS_PROXY=http://proxy.example.com:8080
export HTTP_PROXY=http://proxy.example.com:8080
niuu volundr up
```

### Repository not found

- Verify the repo URL is correct (e.g. `my-org/my-repo`, not the full HTTPS URL)
- Verify your token has access to the repository
- For GitHub Enterprise, make sure the API URL is set correctly in your configuration

---

## Database connection errors

**Symptoms:**

- API server fails to start
- Error: `connection refused` on port 5432

**Fix:**

If using embedded mode, the embedded PostgreSQL may have failed to start:

```bash
# Check if PostgreSQL is running
niuu volundr status

# Try restarting
niuu volundr down
niuu volundr up
```

If using external mode, verify your database configuration:

```bash
# Test the connection
psql -h localhost -U volundr -d volundr
```

---

## Web UI shows blank page or connection error

**Symptoms:**

- Browser shows "connection refused" or blank page at `http://localhost:8080`

**Fix:**

1. Verify Volundr is running:

    ```bash
    niuu volundr status
    ```

2. Check the health endpoint:

    ```bash
    curl http://localhost:8080/health
    ```

    Expected: `{"status": "healthy"}`

3. If the health check fails, check that the API server started on the expected port. Look at the terminal where you ran `niuu volundr up` for error messages.

4. Try a hard refresh in your browser (Cmd+Shift+R / Ctrl+Shift+R).

---

## Persona edit didn't reach sidecar

**Symptoms:**

- You updated a persona via the REST API or UI but the sidecar still uses the old definition.
- `/etc/ravn/config.yaml` on the sidecar shows stale values.

### ConfigMap sync delay (MountedVolume mode)

The kubelet syncs projected ConfigMaps within **~60 s** by default. Edits made through the Volundr REST API are written to the `ravn-personas` ConfigMap; the sidecar will see them within one sync cycle.

**Fix:** Wait 60–90 s after saving, then re-check:

```bash
kubectl exec <pod-name> -c ravn-sidecar -n volundr -- cat /etc/ravn/config.yaml
```

If the value is still stale after 90 s, check the next sections.

### Adapter mismatch

Verify that the Volundr deployment and the sidecar are using the same persona source backend. If Volundr writes to the ConfigMap but the sidecar is configured for HTTP mode (or vice versa), edits will never reach the sidecar.

**Fix:** Check the `personaSource.mode` in your Helm values:

```bash
helm get values volundr -n volundr | grep -A5 personaSource
```

Both the Volundr write path and the sidecar read path must agree on the backend.

### RBAC denied

If the Volundr ServiceAccount lacks permission to patch the `ravn-personas` ConfigMap, edits are silently dropped.

**Fix:** Check the logs:

```bash
kubectl logs deploy/volundr -n volundr | grep -i "forbidden\|rbac\|configmap"
```

If you see `403 Forbidden`, apply the missing RBAC:

```bash
kubectl apply -f charts/volundr/templates/rbac-personas.yaml
```

### PAT expiry (HTTP mode)

If sidecars pull personas via HTTP and the PAT has expired, they will return stale cached values (or `None` if nothing is cached).

**Fix:** Rotate the PAT and update the secret:

```bash
# Issue a new PAT via the Volundr API
curl -X POST http://volundr:8080/api/v1/pats \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"name": "ravn-sidecar"}'

# Update the secret
kubectl create secret generic ravn-volundr-token \
  --from-literal=token=<new-pat> \
  -n volundr --dry-run=client -o yaml | kubectl apply -f -

# Restart sidecars to pick up the new token
kubectl rollout restart deployment/ravn-sidecar -n volundr
```

---

## Sidecar using wrong model

**Symptoms:**

- The sidecar is running a cheaper or weaker model than expected.
- The ravn startup log shows a different model than what you configured in the flow or persona.

### Check the startup log

The ravn sidecar logs the effective config at startup. Look for:

```
INFO ravn.startup: loaded persona='reviewer' model='claude-opus-4-6' thinking=True budget=25
```

```bash
kubectl logs <pod-name> -c ravn-sidecar -n volundr | grep -E "loaded persona|effective|model="
```

### Merge precedence diagram

Three layers determine the effective model (last wins per field):

```
Base persona defaults   (src/ravn/personas/<name>.yaml)
        ↓
Flow-level override     (FlockFlowConfig.personas[i].llm)
        ↓
Stage-level override    (pipeline.stage.persona_overrides.llm)
        ↓
Effective config        (written to /etc/ravn/config.yaml)
```

If a layer is empty (`""` / `0` / `null`), it inherits from the layer below. An empty `model` in the flow config will fall through to the persona default.

**Fix:** Explicitly set `model` in your flow definition:

```yaml
# flock-flows ConfigMap entry
personas:
  - name: reviewer
    llm:
      model: claude-opus-4-6    # always explicit — never rely on inheritance
      thinking_enabled: false
```

### Verify the ConfigMap content

```bash
kubectl get configmap flock-flows -n tyr \
  -o jsonpath='{.data.flows\.yaml}' | grep -A5 "name: reviewer"
```

### In-process dispatch path

If you are using the `RavnDispatcher` in-process path (used by `ReviewEngine`), it applies the same merge logic. If you see the sidecar using the right model but in-process tasks using a different one, check that:

1. The flow is correctly wired into `DispatchService` via `flow_provider`.
2. The `persona_overrides` on the template stage are being applied.

Run the in-process parity test to reproduce and validate:

```bash
pytest tests/integration/test_flock_composition_e2e.py::TestInProcessParity -v
```

---

## Getting more help

If your issue isn't listed here:

1. Check the [API server logs](running.md) for detailed error messages
2. Run with debug logging:

    ```bash
    VOLUNDR_LOG_LEVEL=debug niuu volundr up
    ```

3. File an issue at [github.com/niuulabs/volundr/issues](https://github.com/niuulabs/volundr/issues)
