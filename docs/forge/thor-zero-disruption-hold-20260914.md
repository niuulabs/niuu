# Thor upgrade: HOLD under the zero-disturbance condition

**Verdict: HOLD. No deployment or restart is scheduled.**

The current local-process deployment path necessarily interrupts the API and its
WebSocket proxy connections. Keeping coding processes alive does not meet the
user's stricter requirement that existing sessions **and activities** be
undisturbed. Physics owners also explicitly depend on Forge while operating Mini.
**12:40 UTC is an estimate, not authorization, a reservation or an automatic retry.**

## Authority and scope

Coordinator instruction `a5690ba6-80d1-47fa-8810-876a2ad2bf58` supersedes earlier
maintenance assumptions that allowed a brief reconnect. Safety update
`7064b75a-06d3-475c-ae0e-3583f1da82aa` confirms the hold and permits only a read-only
assessment/plan: no Thor/Forge/Tailscale restart, network change, source/env change,
gateway adoption, stop/start, or resend. No shared change was performed.

The isolated candidate checkout/environment staging completed at approximately
**12:09:18 UTC**, before the latest safety receipt was created at **12:09:59 UTC**,
under the earlier isolated-preflight permission. These artifacts were prepared **inside this worker's `.local/`**. That interrupted command completed; it did not
install a service override or invoke a production launcher. No new lifecycle test
was started in this review, and no further source/environment staging is being
performed. The candidate is not an installed Thor release.

## Concrete mechanics: why this is not seamless

Observed at approximately **12:08–12:14 UTC, September 14**:

- `volundr-forge.service` remains on API PID **61138**, source
  `/home/thor/repos/worktrees/niuu-project-discovery`, docs `c6d80980` over
  `forge-project-discovery-44f85dc3`, source SHA
  `e34593b7c6a10add6e50d62d2f9f3202a940f9d14bdf4819a26761d27610b7b0`.
- Its process directly owns the **0.0.0.0:8080** listener. The installed wrapper
  constructs one `uvicorn.Server`; there is no reload action (`CanReload=no`),
  socket-activated listener (`TriggeredBy` empty), or configured worker/socket
  handoff. The API and proxy share that process.
- `niuu.session_proxy.bridge_websocket` owns both the browser connection and its
  gateway connection inside this process. A replacement API cannot inherit those
  established Python WebSocket tasks/connections through the current restart path.
- `KillMode=process` protects descendants from the service manager's stop signal;
  it does **not** preserve the API's HTTP requests, WebSocket bridges or in-memory
  request state. The wrapper allows up to 15 seconds for graceful shutdown and the
  unit's stop timeout is 20 seconds. Neither is an outage guarantee or total
  restart bound; startup/readiness can add time.
- The listener had **22 established socket rows involving port 8080** in the
  sampled `ss` output. This is not a count of users or proven WebSocket connections;
  the important evidence is that the public listener is in active use.

An API-only restart can therefore temporarily interrupt steering, create,
receipt/message and other control APIs, as well as streaming through the proxy.
A gateway continuing its native turn during that gap does not mean its operator
can interact normally. A client may reconnect and recover persisted output later;
that is recovery **after interruption**, not zero interruption.

## Owners and dependencies observed, not frozen assumptions

- **633 session records**, including archives: 566 archived, 54 stopped, one failed,
  12 running. **12 actual live gateways** were discovered from processes and the
  local state file, not inferred solely from database status. All 12 direct gateway
  health reads matched their session IDs; no terminal-status/live-gateway mismatch
  was found in this snapshot. Future inventory must still check that case.
- **38 protected process identities** were inventoried, including gateway/native
  owners, tmux and two PostgreSQL postmasters. Original identities and API identity
  remained present in the follow-up; no runtime action was taken against them.
- New research runner **`thor:0a713405-79b2-566c-a953-802e3a080c49`** is included in
  the live inventory. Its creation at 12:08:02 preceded this inventory; do not reuse
  an earlier manifest omitting it or later native children.
- Physics coordinator **`thor:2f441fee-583d-580c-b1ec-d51e2fa79655`** and native
  owner **`thor:69c828ae-1512-5dc3-866a-8da74f956895`** explicitly report active
  Forge-hosted Codex/tools, message/receipt APIs and SSH/mesh administration of Mini.
  Their lack of a dependency on the 9500 relay is **not Forge quiescence**.
- PostgreSQL postmasters **4587** and **4594** are running container init processes
  in independent Docker cgroups, publishing host ports **5432** and **5433**
  respectively. This is local Docker-backed database ownership, not Kubernetes.
  The installed API wrapper skips embedded PostgreSQL and performs service-database
  provisioning/bootstrap on startup. It was read, **not executed**, in this review.
  No database query, provisioning, migration, container action or restart was run.
- 9500 HTTP, **9501 trusted HTTPS** and 5300 HTTPS preview returned healthy/stable
  responses. No Tailscale or Mini changes were made. Source/unit/config file hashes
  were captured privately; credentials and raw environment values are not evidence
  artifacts in Git.
- Share with Voice and Dictation replay samples remain readable with **20** and
  **96** complete turn objects respectively; hashes were captured. These samples
  do not prove every historical or in-flight byte is durable.

Spark still reports clean **650088f3 / timeline-v4**. Its latest flow tester
**`spark:2e6286cb-e25a-507d-ad4a-7725879eed9e`** is now **archived**, timestamp
**12:02:20.871167 UTC**, with seven readable replay rows. This supersedes the earlier
running notice. This worker did not archive it and will not resume it.

## Candidate and prior lifecycle evidence

Candidate **650088f3e2fe234e75cd7d2578acae911290ee91**, source SHA
**57446280f7a8bc81d6ae0b8215f8e8df528f0d28add876ac272bd39d3ebff000**,
is the exact Spark-tested source. The private local candidate's tracked tree and
Python source digest match that pin. Its copied environment was not activated as
Thor's service environment; no new claim of a completed Thor-specific rollout
validation is made.

The prior exact staged Spark log/source audit was re-read:
**5,132 passed, 24 skipped, 95 deselected, one expected failure**, 85.77% scoped
statement/branch coverage with the unchanged 85% gate. It includes four real-owned
API/gateway/native-fixture lifecycle cases: running, stopped, archived and failed
stored statuses, graceful and abrupt API death, route reconstruction, same-turn
continuation, output during the outage and exactly one native send. The test source
explicitly makes **two proxy reconnections**. This proves bounded recovery in that
fixture, **not uninterrupted sockets**. Infrastructure database/catalog is mocked;
no provider or physical device is involved.

The startup-composition correction selects the actual local-process adapter's
backend instead of falsely applying Kubernetes cleanup to terminal stored rows.
That addresses the prior session-loss incident, but is separate from API/WS
availability. The current deployed Thor baseline must not be used as an automatic
rollback source without that safety fix.

A previously staged minimal Thor safety-only rollback exists at
`~/.local/share/niuu/releases/forge-local-preservation-c6d80980`, commit
`e8c814f854beb30d00d068622d63a3c3ab244976`. It is a **future rollback candidate**, not
freshly accepted or selected in this review. Re-audit its source/environment and
owned lifecycle behavior before any permitted maintenance. Reverting a source
selection does not restore killed processes or uncertain in-flight operations.

## Least-disruptive ordered plan — not authorization to execute

1. **Keep HOLD now.** Wait for actual owner evidence that the dependent native work
   finished or reached an agreed safe boundary. Do not act at an ETA, trust a stale
   `idle` hint, bypass cross-project receipt ownership, or send synthetic requests
   into anyone's session. Existing sessions and the research runner remain intact.
2. **Review the acceptable availability contract.** With today's implementation,
   any cutover needs explicit acceptance of a bounded API/control/stream reconnect
   gap in a convenient maintenance window. Merely having an available time or green
   preservation tests does not satisfy the current zero-disturbance condition.
   Until that requirement is relaxed or a genuinely seamless mechanism is built
   and validated, deployment remains blocked by the mechanism itself.
3. **Before a subsequently permitted window, freeze and revalidate** exact candidate
   and preservation-safe rollback sources and copied environments on Thor. Exercise
   owned full startup/lifecycle and fail-fast/rollback guards in isolated temporary
   state, including terminal stored rows with live processes. Do not import/run the
   production wrapper as a test: its database bootstrap and background services are
   real side effects. Review those startup effects explicitly before release.
4. **Take fresh complete preservation evidence immediately before mutation.**
   Include all registered session IDs, all actual gateway/native/tmux owners even
   when stored status disagrees, current native children, process start identities,
   proxy routes, direct gateway health, database owners, configuration bytes,
   archived acceptance samples and live retained prefixes/pending input disposition.
   Any new owner, drift or unresolved delivery invalidates the preparation.
5. **Only within an explicitly accepted window**, use the existing fail-closed,
   API-only source-selection procedure with one owned drop-in and `KillMode=process`.
   Keep gateways/native turns running on their existing source; no blanket adoption,
   stop/start, database reset or Tailscale change. The API gap must be honestly
   announced; this procedure cannot be rebranded as seamless.
6. **Verify recovery rather than infer it from health.** Reconnect to the same
   gateway/native session, check preserved identities and known retained output,
   receipt/control availability, direct gateway routes and independent services.
   If an ACK was lost, inspect the original request identity and durable status;
   never resend an uncertain prompt merely to obtain a response. Persisted replay
   does not prove that every unflushed buffer or in-flight network operation survived.
7. **On failure, stop the rollout.** A pre-audited preservation-safe API rollback
   may require a second outage inside the accepted recovery budget. No automatic
   session resurrection, gateway replacement or resend. If an owner process is
   missing, rollback cannot resurrect its memory: report the concrete loss and
   develop an owner-approved recovery using original identities and retained data.

Upgrading the API provides the latest server contracts/archive behavior and source
selection for **new sessions**. Existing gateway processes intentionally retain
their loaded implementation; getting every Codex transport improvement into those
processes needs a separate owner-approved adoption at a saved boundary. Native
live deduplication is also separate: assigned to `ac1b685f` as **UX-016 after current
2264 publication**, not shipped by this server upgrade or mixed into that release.

## If strict zero interruption must remain a permanent requirement

That requires additional generic infrastructure, not a different restart command.
A possible design is a stable front proxy with graceful connection draining and
blue/green API processes: keep old established WebSockets alive until naturally
closed while directing new requests to the new backend. Before that can be safe,
prove single ownership of lifecycle/background jobs and persistent state, compatible
routing/auth/replay, and safe startup/database behavior with overlapping APIs.
Simply starting a second API on the production database/state file is not a safe
experiment. Listener socket activation alone does not transfer active WebSocket
state from a dying process. Introducing a new front proxy at today's directly-bound
port may itself need an initial maintenance window. None of this is implemented,
validated, scheduled or authorized by this assessment.

## Evidence and handoff

Private source of observations:
`.local/thor-zero-disruption-preflight-20260914/` in this runtime checkout.
Key files: `inventory.json`, `database-ownership.json`, `candidate-evidence.json`,
`read-only-followup.json`, `final-owner-inventory.json`, the two coordinator receipts,
and `hold-notification-response.json`. Public summary:
[preflight evidence](thor-zero-disruption-evidence-20260914.json).

OpenClaw HOLD milestone **6701** was verified. One central hourly monitor remains;
no new timer, restart window, background test or automatic retry was created.
Runtime sender: `thor:4ab99660-05e8-52c3-90d3-85d7463ce8a2`;
coordinator recipient: `thor:8f20102d-6da7-58aa-98c2-e0bce2deab97`.
