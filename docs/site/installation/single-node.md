# Single-Node Deployment (k3s)

For DGX Spark, homelab servers, or any single Linux machine. k3s is a production-grade Kubernetes distribution that runs as a single binary.

---

## Install k3s

```bash
curl -sfL https://get.k3s.io | sh -
# Verify
sudo kubectl get nodes
```

k3s bundles Traefik ingress controller and local-path storage provisioner by default.

---

## Set Up Kubeconfig

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $USER:$USER ~/.kube/config
kubectl get nodes
```

---

## Create Namespace and Secrets

```bash
kubectl create namespace volundr

# Anthropic API key
kubectl create secret generic volundr-anthropic-api \
  -n volundr \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-xxx

# Database credentials
kubectl create secret generic volundr-db \
  -n volundr \
  --from-literal=username=volundr \
  --from-literal=password=volundr

# GitHub token (optional)
kubectl create secret generic github-token \
  -n volundr \
  --from-literal=token=ghp_xxx
```

---

## Storage

The k3s default `local-path` provisioner works for single-node. For production durability, consider Longhorn:

```bash
helm install longhorn longhorn/longhorn -n longhorn-system --create-namespace
```

Then set `storage.sessions.storageClass: longhorn` and `storage.home.storageClass: longhorn` in your values.

---

## Deploy PostgreSQL

In-cluster for simplicity, or use an external instance for durability:

```bash
helm install postgresql oci://registry-1.docker.io/bitnamicharts/postgresql \
  -n volundr \
  --set auth.username=volundr \
  --set auth.password=volundr \
  --set auth.database=volundr
```

---

## Deploy Volundr

```bash
helm install volundr oci://ghcr.io/niuulabs/charts/volundr \
  -n volundr --create-namespace \
  --set database.external.host=postgresql.volundr.svc.cluster.local \
  --set database.existingSecret=volundr-db \
  --set ingress.enabled=true \
  --set ingress.className=traefik \
  --set ingress.hosts[0].host=volundr.local \
  --set ingress.hosts[0].paths[0].path=/api/v1 \
  --set ingress.hosts[0].paths[0].pathType=Prefix \
  --set storage.sessions.storageClass=local-path \
  --set storage.home.storageClass=local-path
```

---

## GPU Passthrough (DGX Spark / NVIDIA)

Install the NVIDIA device plugin:

```bash
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.14.1/nvidia-device-plugin.yml
```

Verify the GPU is detected:

```bash
kubectl get nodes -o jsonpath='{.items[0].status.allocatable}' | jq '.["nvidia.com/gpu"]'
```

Create a GPU profile in your Helm values:

```yaml
profiles:
  - name: gpu-heavy
    description: "GPU-accelerated session"
    workloadType: session
    model: "claude-sonnet-4-20250514"
    resourceConfig:
      cpu: "2"
      memory: "8Gi"
      gpu: "1"
    isDefault: false
```

---

## DNS

Add `volundr.local` to `/etc/hosts` pointing to your server IP:

```
192.168.1.100  volundr.local
```

Or use a real domain with DNS pointing to the server.

---

## TLS

For TLS, install cert-manager and add TLS configuration to ingress:

```bash
helm install cert-manager jetstack/cert-manager \
  -n cert-manager --create-namespace \
  --set crds.enabled=true
```

Then add to your Helm values:

```yaml
ingress:
  tls:
    - secretName: volundr-tls
      hosts:
        - volundr.example.com
```

---

## Verify

```bash
kubectl get pods -n volundr
curl http://volundr.local/health
```
