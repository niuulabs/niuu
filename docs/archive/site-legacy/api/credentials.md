# Credentials API

Credentials are stored in a pluggable backend (Vault, Infisical, or in-memory). The API only exposes metadata — secret values are never returned.

All endpoints are prefixed with `/api/v1/credentials`.

## User credentials

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/types` | List credential types with field info |
| `GET` | `/` | List current user's credentials (metadata) |
| `GET` | `/{name}` | Get credential metadata |
| `POST` | `/` | Create a credential |
| `DELETE` | `/{name}` | Delete a credential |

## Tenant credentials (admin)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tenant/list` | List tenant shared credentials |
| `POST` | `/tenant` | Create tenant credential |
| `DELETE` | `/tenant/{name}` | Delete tenant credential |

## Credential types

| Type | Description |
|------|-------------|
| `api_key` | API key (single key field) |
| `oauth_token` | OAuth access/refresh tokens |
| `git_credential` | Git username/password or token |
| `ssh_key` | SSH private/public key pair |
| `tls_cert` | TLS certificate and key |
| `generic` | Arbitrary key-value pairs |

## Secret mounting

Credentials can be mounted into session pods via the secret injection adapter. Mount types:

- `env_file` — mounted as a `.env` file
- `file` — mounted as a file at a specific path
- `template` — rendered from a Go template
