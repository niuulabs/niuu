# Production Checklist

Use this checklist before running Niuu for real operators.

- Configure OIDC identity.
- Configure authorization policy.
- Use an external database with backups.
- Use a real secret backend.
- Limit credentials exposed to sessions.
- Configure ingress and TLS.
- Set resource requests and limits.
- Enable logs, metrics, and traces.
- Review Helm values.
- Test session creation, review, archive, and recovery.

Do not treat the local stack as production hardening.
