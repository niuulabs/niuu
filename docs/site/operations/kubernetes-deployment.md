# Kubernetes Deployment

Deploy Niuu to Kubernetes with the Helm charts.

## Umbrella chart

```bash
helm install niuu ./charts/niuu -n niuu \
  --set database.external.host=postgres.svc.cluster.local \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=niuu.example.com
```

Upgrade an existing release:

```bash
helm upgrade niuu ./charts/niuu -n niuu
```

## Production notes

Before production, configure identity, secrets, database backups, ingress, TLS, resource limits, and observability.
