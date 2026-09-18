# Forge dashboard loading

The September 18 Sessions fix did not remove the dashboard's combined metrics and
resource reads. A clean-browser run of `/volundr/forge` took **30.98 seconds** to
show any panel, although healthy session inventories arrived within one second.
The dashboard waited on stats, clusters and sessions together. Its cluster adapter
also made a second, combined session request and silently swallowed read failures.

## Change

- Render the dashboard and Quick launch immediately. Session rows, metric tiles and
  resource rows update independently as data arrives.
- Reuse the Sessions registry query and request stats/resources per registered host.
  Successful snapshots remain visible through a failed refresh, labelled as saved
  data. Missing hosts are named; totals explicitly cover only loaded hosts.
- Apply the existing configurable eight-second host-read deadline, cancel requests
  when leaving the page, refresh metrics every thirty seconds, and offer Retry.
  The runtime configuration is `services.forge.sessionListTimeoutMs`.
- Extend `/api/v1/forge/stats` and `/api/v1/forge/cluster/resources` to honor
  `instance_id`. Resolve the selected host through the existing visibility rules;
  propagate selected-host failures instead of returning successful zero totals.
  Calls without a selector retain the existing aggregate contract.
- Include host identity in scoped metric responses. The web adapter verifies scoped
  stats and resource responses, rejecting older gateways that ignore selection rather
  than counting a fleet aggregate once per host. Update the gateway with the UI.
- Remove the cluster adapter's silent error substitutions. A failed resource read
  now stays a visible error instead of turning into an apparently healthy empty host.

## Validation and rollout

Unit tests cover independent readiness, partial totals and sparklines, cancellation,
deadlines, stale snapshots, retry, registry failure/removal, scoped adapter calls and
rejection of unscoped responses. API tests cover selected-host isolation, visibility,
remote failures and malformed payloads. Desktop and iPhone browser tests keep healthy
hosts usable while another host hangs, then verify recovery and updated totals.

The API candidate is a copy of the currently running immutable release with only the
two route changes applied. It does not roll out the branch's other pending gateway
or runtime changes. The repository's isolated restart fixture verifies graceful and
abrupt API restarts, unchanged gateway/native-process identities, in-flight turn
continuity and WebSocket replay for running, stopped, archived and failed stored rows.
Live rollout separately checks the existing gateway process identities.
