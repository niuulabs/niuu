# Quick Start

Get Volundr running in under 5 minutes. Pick the path that fits your setup.

---

## Option 1: CLI (Local Mode)

The fastest way to try Volundr. No Kubernetes required.

### 1. Download the CLI

Grab the latest binary from [GitHub Releases](https://github.com/niuulabs/volundr/releases):

```bash
# macOS (Apple Silicon)
curl -fsSL https://github.com/niuulabs/volundr/releases/latest/download/niuu-darwin-arm64 -o niuu
chmod +x niuu
sudo mv niuu /usr/local/bin/

# macOS (Intel)
curl -fsSL https://github.com/niuulabs/volundr/releases/latest/download/niuu-darwin-amd64 -o niuu
chmod +x niuu
sudo mv niuu /usr/local/bin/

# Linux (x86_64)
curl -fsSL https://github.com/niuulabs/volundr/releases/latest/download/niuu-linux-amd64 -o niuu
chmod +x niuu
sudo mv niuu /usr/local/bin/

# Linux (ARM64)
curl -fsSL https://github.com/niuulabs/volundr/releases/latest/download/niuu-linux-arm64 -o niuu
chmod +x niuu
sudo mv niuu /usr/local/bin/
```

### 2. Initialize

```bash
niuu volundr init
```

The wizard asks a few questions:

| Prompt | Options | Notes |
|--------|---------|-------|
| Runtime | `local`, `docker`, `k3s` | Start with `local` |
| Anthropic API key | Your key | Used for AI sessions |
| Database mode | `embedded`, `external` | `embedded` bundles PostgreSQL |
| GitHub token | Personal access token | For repo access |
| GitHub orgs | Comma-separated | Which orgs to surface |
| GitHub API URL | `https://api.github.com` | Change for GitHub Enterprise |

### 3. Start everything

```bash
niuu volundr up
```

This starts PostgreSQL (if embedded), the Python API server, and a reverse proxy. Open [http://localhost:8080](http://localhost:8080) when it's ready.

That's it. Jump to [Local Quickstart](local-quickstart.md) for a full end-to-end walkthrough, or [First Session](first-session.md) to create your first AI coding session.

---

## Option 2: k3d (Local Kubernetes)

For people who want the full Kubernetes experience without a cloud bill.

### 1. Install prerequisites

```bash
# Install k3d
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash

# Install Helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

### 2. Create a cluster

```bash
k3d cluster create volundr \
  --port "8080:80@loadbalancer" \
  --port "8443:443@loadbalancer"
```

### 3. Install Volundr

```bash
helm install volundr oci://ghcr.io/niuulabs/charts/volundr \
  --namespace volundr --create-namespace \
  --set anthropic.apiKey=sk-ant-... \
  --set github.token=ghp_...
```

### 4. Wait for pods

```bash
kubectl -n volundr get pods -w
```

Once everything shows `Running`, open [http://localhost:8080](http://localhost:8080).

---

## Next steps

- [Local Quickstart](local-quickstart.md) -- end-to-end walkthrough from zero to working session
- [First Session](first-session.md) -- create and use your first AI coding session
- [Installation](installation.md) -- full installation details and prerequisites
- [Configuration](configuration.md) -- all configuration options
- [Troubleshooting](troubleshooting.md) -- common issues and solutions
- [Helm Deployment](../deployment/helm.md) -- production Kubernetes deployment
