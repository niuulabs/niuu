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

## Remote registry selector regression

The independent inventory requests exposed a backend forwarding bug on September
18. Thor used its registry's `instance_id` to choose Build Bro, then forwarded the
same selector to Build Bro's Forge facade. That facade has a different local
registry ID and returned 404. Thor's aggregate response discarded the error and
reported a successful empty list. Spark was affected by the same mechanism.

Direct read-only checks found seven non-archived and two archived sessions on
Build Bro, and nine non-archived and six archived sessions on Spark. Thor's selected
inventories reported zero for each. This was a routing failure; the underlying
sessions were still present.

The correction consumes `instance_id` at the receiving gateway and preserves the
remaining query parameters, including repeated filters and archive selection.
Selected inventory requests now report remote HTTP, transport, and malformed-data
failures instead of clearing a host's list. Existing UI error/stale-list handling
can therefore keep previous rows visible and offer retry. Registry visibility is
still checked before contacting a selected host.

Regression tests exercise two actual facade routers with different registry IDs,
archive filters, unrelated offline hosts, duplicate query parameters, access
checks, and upstream failure responses. A read-only test of the immutable API
candidate against the real Build Bro and Spark endpoints returned exactly the
same session IDs as direct requests for all four inventories. Both the candidate
and rollback pass the four-case isolated restart-preservation fixture.

Unlike the original UI-only change, this correction needs a Thor API release.
The September 18 candidate was staged without the later Claude tmux change. It
was superseded by the combined September 19 release below. Existing gateways
retain their loaded code and are preserved by the API-only release procedure.

## Guild-to-Volundr verification and release — September 19

All five enabled Forge hosts were correctly registered in Guild. Build-Kit had
become reachable at `http://100.90.20.64:8080`, so its missing session exposed the
same selector-forwarding defect as Build Bro and Spark. Build's older server
accepted the forwarded selector and continued returning its two sessions.

The actual frontend path was verified in a clean browser:

1. Guild reads `/api/v1/niuu/instances`.
2. Volundr discovers enabled Forge hosts through
   `/api/v1/niuu/instances?kind=volundr&enabledOnly=true`.
3. The session reader issues independent current/archive requests to
   `/api/v1/forge/sessions?instance_id=<Guild ID>` for every discovered host.
4. Thor resolves that ID in its own registry, consumes the selector, forwards the
   remaining filters, and returns the remote rows with the selected owner's identity.

Before deployment, the browser requested all five hosts but received successful
empty lists for Build Bro, Build-Kit and Spark. No Guild registration or frontend
filter change was needed. Direct reads and the public Tailscale API now agree on
every session ID and owner:

| Host | Current sessions before | Current sessions after | Archived sessions after |
| --- | ---: | ---: | ---: |
| Thor | 11 | 11 | 632 |
| Build | 2 | 2 | 2 |
| Build Bro | 0 | 8 | 2 |
| Build-Kit | 0 | 1 | 0 |
| Spark | 0 | 9 | 6 |

These are the observed September 19 counts; current includes stopped/failed rows
but excludes archived rows. The browser check used **All** to include Spark's nine
stopped sessions. The same published UI (`b6449453`) displays all 20 remote current
rows after the API correction, including `kit-build-testing` on Build-Kit, with no
page errors. Browser checks blocked non-read HTTP requests and outbound socket
controls; no prompt, registration edit, or session lifecycle operation was sent.

Thor adopted immutable release `forge-guild-sessions-20260919-v2`, revision
`ea803c53e50b5387571dc354eec03ebfaa2e80c5`, at 16:26 UTC through the
[guarded release procedure](local-api-release.md). It combines the existing
preservation-safe source, host-scoped dashboard API, registry-selector correction,
preview-query forwarding, and Claude tmux startup/readiness fixes. The earlier
dashboard, routing-only, and trust-only release candidates are superseded.

All **201 candidate checks** passed, covering the Forge facade, Claude startup,
real tmux, and API release/preservation guards. The safe rollback independently
passed all four restart-preservation cases. The exact release's router also
matched direct current/archive reads from all four remote hosts before cutover.
The guarded live cutover preserved **47 protected processes**, **16 live gateways**,
**673 stored session records**, and the selected inactive replay sample. Health is
clean with no failed plugins. Existing gateway processes keep their loaded code;
new Thor gateways use the released Claude startup handling. Dashboard static UI
publication and the remaining gateway-level image changes are separate work.
