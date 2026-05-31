# Authorization

Authorization is pluggable via the `AuthorizationPort`. The adapter checks whether a principal can perform an action on a resource.

## Adapters

| Adapter | Description |
|---------|-------------|
| `AllowAllAuthorizationAdapter` | Permits everything (default, development) |
| `SimpleRoleAuthorizationAdapter` | Role-based checks using tenant membership |
| `CerbosAuthorizationAdapter` | Delegates to a Cerbos PDP |

### Allow all (default)

```yaml
authorization:
  adapter: "volundr.adapters.outbound.authorization.AllowAllAuthorizationAdapter"
```

### Simple role-based

```yaml
authorization:
  adapter: "volundr.adapters.outbound.authorization.SimpleRoleAuthorizationAdapter"
```

Uses the principal's roles and tenant membership to make decisions. No external service required.

### Cerbos

```yaml
authorization:
  adapter: "volundr.adapters.outbound.cerbos.CerbosAuthorizationAdapter"
  kwargs:
    url: "http://cerbos:3593"
```

Delegates decisions to a [Cerbos](https://cerbos.dev/) Policy Decision Point. Cerbos policies define fine-grained access rules.

## How it works

Every protected endpoint extracts the `Principal` from the JWT and creates a `Resource` (kind + id + attributes). The authorization adapter is called:

```python
allowed = await authorization.is_allowed(principal, "read", resource)
```

For list endpoints, `filter_allowed` filters results to only those the principal can access.
