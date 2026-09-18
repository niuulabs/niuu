# Forge web terminal access review

Reviewed on Thor on 2026-09-17 for `forge/ux-improvement`.

The current local-process Forge deployment does not provide the terminal service
contract used by the web Terminal tab. A working chat connection does not imply
a working terminal. Changing the browser URL or Tailscale configuration alone
will not enable it.

## Read-only observations

| Forge host | Session health through the web proxy | Terminal session-list endpoint                                     |
| ---------- | ------------------------------------ | ------------------------------------------------------------------ |
| Thor       | HTTP 200                             | HTTP 404, JSON `Not Found`; same result directly on localhost:8080 |
| Build      | HTTP 200                             | HTTP 200 with the application's HTML fallback, not terminal JSON   |
| Build Bro  | HTTP 200                             | HTTP 200 with the application's HTML fallback, not terminal JSON   |

Spark had no active session available for the same probe. These checks issued
GET requests only. No terminal was created and no command was executed.

## Where the mismatch is

- `web-next/packages/ui/src/chat/transport.ts` derives a terminal WebSocket URL
  from the session chat endpoint. `SessionTerminalLive.tsx` then expects
  `/s/{session_id}/terminal/api/terminal/sessions`, terminal spawn/kill routes,
  and `/s/{session_id}/terminal/ws/{terminal_id}`.
- `src/niuu/session_proxy.py:register_session_proxy_routes` registers the session
  and Ravn sockets, session `/api/*` HTTP routes, and health. It does not register
  session `/terminal/*` HTTP or WebSocket routes.
- `src/volundr/adapters/outbound/local_process.py` starts Skuld and applicable
  Ravn processes. It does not provision a terminal daemon for each workspace.
- `containers/devrunner/terminal.py` already implements the terminal REST and
  WebSocket protocol with managed tmux sessions. The Kubernetes runtime wires
  that service through `/terminal/` to loopback port 7681 in
  `src/volundr/adapters/outbound/direct_k8s_pod_manager.py`. That container setup
  is not present in the local-process runtime.
- Thor's outer web nginx already forwards `/s/*` and remote Forge prefixes,
  including WebSocket upgrade headers. The missing pieces are the runtime
  terminal service and the inner session routing.

## Work required for local and remote Forge terminals

1. Provision a managed per-session terminal service in the local-process runtime,
   reusing or adapting the devrunner implementation. Set the workspace, runtime
   user and environment; track its port and lifecycle; clean up on session stop.
2. Add session-authorized terminal REST and WebSocket proxy routes in Niuu.
   Apply the same identity, tenant and session ownership checks as the chat proxy.
   Route to the correct host and service without exposing an unauthenticated PTY
   port to the network.
3. Advertise terminal availability in session capabilities so the browser does
   not infer support from the chat URL.
4. Verify authenticated remote access over the existing HTTPS/Tailscale origin:
   restore/create terminals, stream output, send input, resize, reconnect, switch
   sessions, close terminals, and enforce read-only and cross-session access.

The browser now distinguishes unsupported hosts (404 or HTML fallback) from
request failures, offers retry, and avoids spawning a shell after a failed list
request or in read-only mode. It waits for terminal initialization fonts before
connecting its socket. Terminal is hidden by default and can be enabled under
**Settings → Sessions → Session tabs**. Those UI changes do not create the missing
host service; remote terminals remain unavailable until the runtime work above
is implemented.
