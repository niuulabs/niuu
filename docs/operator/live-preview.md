# Live preview

A dev server the agent starts inside a Forge session (a Kubernetes pod, a
session VM, or a local process in mini mode) opens in the browser on its own
origin:

```
https://<port>--<session-id>.<preview domain>/
```

Nothing has to be registered: Skuld notices the listening port, the session
page shows a **Preview :5173** chip, and the app runs at `/` with no path prefix.
Only the session's owner can open it.

## How a request travels

1. The browser asks for `5173--<id>.preview.example.com`. The gateway signs the
   user in on the preview domain (standard OIDC; see [Sign-in](#sign-in)) and
   forwards the access token to Forge.
2. Forge's **preview router** (`niuu.preview_proxy`) reads the session and port
   from the `Host` header and checks that the caller owns the session: the same
   Cedar decision the `/s/{id}` session proxy makes, plus "owner" — a room
   participant may watch the chat, but the app's origin is the owner's.
3. The router removes the browser's `Authorization` and `Cookie` (and any
   `x-auth-*` identity headers), so the platform's credentials never reach the
   app. It mints a **router credential** for that one session and sends the
   request to the session's own Skuld as `/preview/<port>/<path>`.
4. Skuld verifies the router credential, checks the port is one it discovered,
   and forwards to that port **on loopback only** (`127.0.0.1`, or `::1` for an
   IPv6-only listener). It presents the request as coming from
   `localhost:<port>`, so dev servers that check `Host` (Vite) accept it.
5. On the way back, the router removes every `Domain=` attribute from
   `Set-Cookie`. An app can only set host-only cookies on its own preview
   origin; it cannot toss a cookie onto the parent domain.

HTTP bodies stream both ways and WebSockets (hot reload) are bridged frame for
frame, so Vite's HMR works. Only connecting is bounded
(`session_preview.upstream_connect_timeout_seconds` at Forge,
`preview.connect_timeout_seconds` at Skuld); an idle stream is closed after the
chart's `streamIdleTimeout` (one hour by default), after which the dev server's
client reconnects.

## The router credential

The router authenticates to Skuld with the platform's existing workload
identity, not a new secret or token kind: a short-lived `valkyrie_build`
workload JWT with

- `sub` = the session owner,
- `scopes: ["forge:session:preview"]`,
- `workload_session_id` = the one session it may reach.

`forge:session:preview` is an **issuer-only** scope
(`niuu.domain.services.token_scope.ISSUER_ONLY_SCOPES`): the workload-identity
exchange never grants it, so only Forge itself can mint one. Skuld checks the
signature against the workload JWKS (`preview.router_auth`,
`skuld.preview_adapters.WorkloadJwtRouterAuth`) whether or not an Envoy sits in
front, then the scope and the session binding. A browser's own token never
carries the scope, so the browser-facing `/s/{id}/preview/...` path is refused
(403). Where a pod's Envoy enforces authorization, its gateway route admits the
credential only on `/preview/`.

## Port discovery

Skuld scans for listening TCP sockets every `preview.port_scan_interval_seconds`
and sends a `preview_ports` frame (`ports`, `opened`, `closed`) on the session's
event stream whenever the set changes. `GET /api/preview/ports` returns the
current list, the scan interval and any scan failure (`scan_failures`,
`last_error`; every failure is also logged at error).

| Where the session runs | Scanner | What counts as the session's |
|---|---|---|
| Kubernetes pod, session VM | `ProcNetListeningPortScanner` (`/proc/net/tcp`, `tcp6`) | Every listener in the network namespace, except Skuld's own sockets and `excluded_ports` (the skuld chart excludes the pod's nginx, broker, Envoy, authz, terminal, mesh and resident ports automatically, plus `preview.excludedPorts`, `[5432]` by default) |
| Mini mode (a shared laptop or host) | `LsofSessionPortScanner` (`lsof`, `ps`) | Listeners of Skuld's descendants, and of processes whose environment carries the session's `SKULD__SESSION__ID` (a server the agent started with `nohup … &` is re-parented by the OS but keeps that environment) |

A listener bound to one specific non-loopback address is not offered: Skuld
only dials loopback.

## Sign-in

The preview domain is a different site from the platform, and the web app keeps
its tokens in `localStorage`, not cookies — so the preview needs its own
sign-in. The volundr chart's `sessionPreview.oidc` renders an Envoy Gateway
`SecurityPolicy` on the preview route:

- one callback host, `auth.<preview domain>`, serves every preview, so the
  provider needs one redirect URI: `https://auth.<preview domain>/oauth2/callback`;
- the session cookies are scoped to the preview domain (`cookieDomain`), never
  to the platform's parent domain, and are `HttpOnly`;
- the access token is forwarded to Forge as a bearer token, which Forge's Envoy
  verifies like any other request, and which the router then strips.

The client must be confidential and its access tokens must carry an audience
Forge's Envoy accepts (`volundr-api`) and the tenant and roles claims Niuu reads.
For Keycloak, `ymir/security/keycloak/configuration/preview-client.py` in the
infrastructure repository creates it and stores its secret.

## Deploying

Forge (volundr chart):

```yaml
workloadIdentity:
  enabled: true            # the router credential
sessionGateway:
  enabled: true
  certIssuer: letsencrypt-production   # must solve DNS-01 for the wildcard
sessionPreview:
  enabled: true
  domain: preview.example.com          # default: preview.<global.domain>
  oidc:
    issuer: https://keycloak.example.com/realms/volundr
    clientId: niuu-preview
    clientSecretName: niuu-preview-oidc  # key "client-secret"
```

This adds a `preview-https` listener for `*.<domain>` to the session gateway,
an HTTPRoute to Forge, and the SecurityPolicy. Point a wildcard DNS record for
`*.<domain>` at the session gateway's address. A DNS server that cannot hold
wildcard records (dnsmasq, and so UniFi's external-dns webhook) needs the record
at the upstream provider instead.

Session pods (skuld chart, usually through the pod manager's
`session_defaults`): `preview.enabled: true`. It needs
`envoy.jwt.workload.issuer` and `jwksUri`, which Forge already renders for
session pods when workload identity is on.

### Mini mode

The local host serves previews on `*.localhost` at its own port, for example
`http://5173--<id>.localhost:8080/`. Browsers resolve every `*.localhost` name
to loopback, so this needs no DNS, certificate or sign-in
(`host.session_preview`, on by default). On a host without an identity provider
(`host_auth.mode: none`) local sessions use `NoRouterAuth`; on an authenticated
host set the pod manager's `preview_router_auth` to a `WorkloadJwtRouterAuth`
for the host's workload JWKS, or local sessions run without live preview (logged
at session start).

### Hosts reachable only by IP

A host with no DNS name can still give each preview its own origin with an
[sslip.io](https://sslip.io)-style wildcard that encodes the address, for
example `session_preview.domain: preview.192-168-1-20.sslip.io`, so
`5173--<id>.preview.192-168-1-20.sslip.io` resolves to `192.168.1.20`. Without
a certificate for that name the preview is served over plain HTTP; the origin
isolation (host-only cookies, no platform credentials) still holds, but the
traffic is not encrypted, so keep it to a trusted network. This is documented,
not built in.

## Not covered

- The devrunner's `/s/<id>/svc/<name>/` proxy still exists. It serves an app on
  the session API's own origin, which lets that app read and act with the
  platform's session context; it should be retired in favour of live preview.
- Session VMs run the same Skuld scanner and forwarder; Forge reaches a VM's
  Skuld through the VM runtime's existing target. A VM Skuld needs the workload
  JWKS reachable and its `preview` settings rendered by the VM runtime.
