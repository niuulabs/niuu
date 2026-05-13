# Connecting Tyr to Volundr

Tyr can dispatch sessions to Volundr autonomously using a **Personal Access Token (PAT)**. When a PAT is stored as a credential, Tyr's dispatcher operates without requiring an inbound HTTP request to carry a Bearer token.

## 1. Create a PAT in Volundr

1. Open **Volundr → Settings → Access Tokens**.
2. Click **Create Token**.
3. Give the token a descriptive name (e.g. `tyr-dispatcher`).
4. Copy the generated JWT — you will not see it again.

## 2. Add the PAT in Tyr

1. Open **Tyr → Settings → Integrations → Volundr**.
2. Select **Add Connection** with type `code_forge`.
3. Paste the PAT into the credential field (`api_key`).
4. Set the Volundr base URL if it differs from the default (`http://volundr:8000`).
5. Enable the connection.

For multi-cluster setups, repeat this once per Volundr instance and give each
connection a distinct `config.name` such as `mac-mini`, `macbook-pro`, or
`office-mini`.

In anonymous local dev mode, `code_forge` connections may also be created with
an empty credential value. Tyr will then talk to Volundr without a Bearer token.
This is intentionally insecure and should only be used on trusted local/dev
networks.

Behind the scenes this creates an `IntegrationConnection` of type `CODE_FORGE` and stores the PAT in the credential store under the connection's `credential_name`.

## 3. Verify autonomous dispatch

Once configured, Tyr's `VolundrAdapterFactory` resolves the stored PAT automatically:

```
VolundrAdapterFactory.for_owner(owner_id)
  → looks up CODE_FORGE connection for the owner
  → retrieves api_key from credential store
  → returns VolundrHTTPAdapter(base_url=..., api_key=<pat>)
```

Every `spawn_session` call made by the adapter includes `Authorization: Bearer <pat>` without any manual `set_auth_token()` call.

To verify, trigger a dispatch for the owner and confirm that the session is created in Volundr.

## Anonymous local Tyr with multiple Volundrs

If you want one local Tyr UI to drive multiple Volundr instances without a real
auth proxy, use:

```yaml
auth:
  allow_anonymous_dev: true
  default_user_id: dev-user

volundr:
  use_connection_factory_in_dev: true
  trusted_connection_test_urls:
    - http://mac-mini.local:8080
    - http://macbook-pro.local:8080
```

Without `use_connection_factory_in_dev: true`, anonymous dev Tyr falls back to
the single local Volundr adapter and ignores stored `CODE_FORGE` connections.

## How runtime tokens interact with stored PATs

When a user dispatches via Tyr's HTTP API (manual dispatch), the inbound Bearer token is forwarded to Volundr via `set_auth_token()`. This **overrides** the stored PAT for that request. Once the request completes, `clear_auth_token()` restores the stored PAT as the default.

| Scenario | Authorization header sent to Volundr |
|----------|--------------------------------------|
| Autonomous dispatch (PAT only) | `Bearer <stored-pat>` |
| Manual dispatch (runtime token) | `Bearer <runtime-token>` |
| No PAT, no runtime token | *(none — request is unauthenticated)* |
