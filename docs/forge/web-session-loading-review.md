# Sessions loading and credential requests

Reviewed on 2026-09-18 for `forge/ux-improvement`.

## Reproduction

Opening a real Thor conversation in a clean Chromium profile took **34.2 seconds**.
Both combined active and archived inventory requests waited about **30.5 seconds**.
The registry contained Thor, Build, Build Bro, Spark and an unreachable Build-Kit.
A direct Thor inventory request took approximately **0.26 seconds**.

The server's combined inventory awaits every registered Forge. One unavailable host
therefore delays the complete response. This review changes the web client's request
strategy; the combined server endpoint remains available for other consumers.

Guild also requested `/api/v1/niuu/credentials/user` and `/tenant`, which returned
404. The configured credential service is `/api/v1/credentials`; both correct scope
endpoints returned 200.

The supplied MetaMask errors originate in injected extension code. The
`contentscript.js` listener warnings also appear extension-related: neither those
warnings nor the MetaMask errors appeared in the clean-browser reproduction, and
the application does not contain that extension code. Raising an EventEmitter
listener limit would not address the measured inventory bottleneck.

## Changes

- Discover enabled Forge hosts from the existing Guild registry, then load active
  and archived inventories independently with `instance_id` queries. Display each
  available host immediately; archives never block the active list.
- Preserve a requested session while other hosts load, instead of briefly selecting
  a different host's first session. Route known active and archived session detail
  requests directly to their owner.
- Cancel abandoned requests. Apply an eight-second per-inventory deadline, configurable
  through `services.forge.sessionListTimeoutMs` in runtime configuration.
- Show loading, unavailable and stale-list states per host, with an explicit retry.
  Registry errors remain visible even when a previous host list is cached. Successful
  active inventories refresh every five seconds; archives every minute; failed hosts
  and the registry refresh every thirty seconds.
- Keep previously fetched rows visible during temporary failures, clearly marked as
  a saved list. Remove a host's rows when it is removed from the registry.
- Resolve Guild credentials from the configured credential service, request only the
  selected registration scope, and report failures with a retry instead of describing
  a failed request as an empty credential list.

## Verification

The production build against the same real hosts opened the conversation in
**4.2 seconds**, with healthy inventory responses arriving within **0.6 seconds**
of navigation. Build-Kit appeared as unavailable without delaying those hosts.
There were no browser warnings or page errors in either clean-browser run. These
are individual observed timings, not a performance guarantee.

Regression coverage includes healthy-plus-stuck hosts, timeout and cancellation,
stale data and registry errors, retry and deregistration, archive priority and
owner routing, preserving the requested selection, credential scope paths and
permission failures. Desktop and phone browser checks cover progressive loading
and recovery alongside existing history paging, image previews and session controls.

Deployment requires only the rebuilt static web app and an nginx reload. It does
not restart Forge, Skuld, or any active session.
