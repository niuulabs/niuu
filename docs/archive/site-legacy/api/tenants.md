# Tenants & Users API

Volundr supports hierarchical multi-tenancy with role-based access control.

All endpoints are prefixed with `/api/v1/identity`.

## Identity

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/me` | Get current authenticated user |
| `GET` | `/auth/config` | Get public auth discovery metadata |
| `GET` | `/settings` | Get current user settings schema |
| `GET` | `/users` | List all users (admin) |

## Tenant management

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tenants` | List tenants (optional parent_id) |
| `POST` | `/tenants` | Create tenant (admin) |
| `GET` | `/tenants/{id}` | Get tenant |
| `PATCH` | `/tenants/{id}` | Update tenant (admin) |
| `DELETE` | `/tenants/{id}` | Delete tenant (admin) |

## Membership

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tenants/{id}/members` | List members |
| `POST` | `/tenants/{id}/members` | Add member (admin) |
| `DELETE` | `/tenants/{id}/members/{user_id}` | Remove member (admin) |

## Provisioning

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/users/{id}/reprovision` | Re-provision user storage (admin) |
| `POST` | `/tenants/{id}/reprovision` | Re-provision all users in tenant (admin) |

## Tenant hierarchy

Tenants form a tree via `parent_id`. Each tenant has:

- **Tier**: `developer`, `team`, or `enterprise`
- **Quotas**: `max_sessions` and `max_storage_gb`
- **Path**: materialized path for fast ancestor lookups

## Roles

| Role | Permissions |
|------|------------|
| `volundr:admin` | Full access, tenant management, user provisioning |
| `volundr:developer` | Create/manage own sessions, view tenant resources |
| `volundr:viewer` | Read-only access |

Users are JIT-provisioned on first login. The identity adapter extracts claims from the JWT and maps IDP roles to Volundr roles via the `role_mapping` config.
