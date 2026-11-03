# Lexi push gateway on Thor

Thor's Forge is the push gateway for Damien's Guild: it sends LexiChat's alerts, Live
Activity updates, widget reloads and background pushes for every node (Thor and mini-m6).
Damien approved it on 2026-10-31. How the gateway itself works is in
[forge-notifications.md](forge-notifications.md), section "Apple push".

## What is configured where

| What | Where |
|---|---|
| Gateway config | Thor, `~/.config/niuu-forge-thor/config.yaml`, section `push:` (file mode 600) |
| APNs auth key (`.p8`) | Thor, `~/.openclaw/secrets/apns/`: the only `.p8` there, mode 600 |
| Who else uses the key | lexi-agent-service (VoIP pushes): the same team-scoped "Forge Push" key |
| Device registry | Thor's Volundr database, table `device_tokens` (`push.device_registry: postgres`) |
| Push on mini-m6 | **off** (no `push:` section), so nothing is pushed twice |

The `push:` section:

```yaml
push:
  enabled: true
  device_registry: postgres
  apns:
    team_id: 828357L924
    key_id: <the key's id: the 10 characters in its file name, AuthKey_<id>.p8>
    bundle_id: com.xteo.lexi.chat
    private_key_path: <the .p8 above>
  gateway:
    enabled: true
    guild_url: http://127.0.0.1:8080          # Thor's own Guild facade (same process)
    public_api_url: http://thor.tail737f2a.ts.net:8080
    origin_label: Thor
    catch_up_items: 0                         # start at each node's newest notification
```

- The key's id and contents never go into a ticket, log, commit or message. Only the
  config file names them.
- TestFlight and App Store builds register `production` tokens, which go to
  `api.push.apple.com`. Debug builds register `sandbox` tokens, which go to the sandbox
  host. The token's own `environment` picks the host.
- `stream_enabled` stays off: the Guild runs in the gateway's own process. The gateway
  wakes on Thor's own notifications at once and reads mini-m6 every 5 s.

**Devices.** The app registers its tokens through the Guild with
`POST /api/v1/forge/devices`: alert, push-to-start, widget, and one Live Activity token per
running activity. Alert tokens reach every node; the other kinds go to the gateway alone.
A `widget_fallback` token (one the app read with `WidgetCenter.currentPushInfo` instead of
getting it from the widget extension's handler) is stored and counted per environment in
`/push/status`, and never pushed to: it tells the two sources apart (LEX-1186). Registration
and prune log lines name each token by a 12-hex SHA-256 fingerprint, never by value, so two
registrations can be compared.
Check them on Thor:

```bash
docker exec volundr-pg psql -U volundr -d volundr -c \
  "SELECT kind, environment, count(*), max(updated_at) FROM device_tokens GROUP BY 1, 2"
```

## Checking it

```bash
curl -s http://127.0.0.1:8080/api/v1/forge/push/status | python3 -m json.tool
```

- `running: true`, with Thor's and mini-m6's ids in `nodes`, is the healthy state.
- **Spark** (`582bf8d0`) is in `feedless_nodes`: it runs an older Forge without the
  notifications feed. That only skips Spark; the gateway keeps pushing for the other nodes
  and asks Spark again every `feedless_recheck_seconds` (an hour). Before LEX-1738 it showed
  in `unreachable_nodes` ("feed answered HTTP 502") and was asked every 30 s.
- The first tick after a restart can count one `loop_errors` ("Guild registry is
  unreachable: ConnectError"), because it runs before the API is listening; the next tick
  reads the Guild.
- `nodes` holds the gateway's position per node. The counters cover alerts sent,
  suppressed and failed, and Live Activity, widget and background pushes.
- Per APNs environment (each token goes to the host of its own environment):
  - `tokens_by_environment`: registered tokens, e.g.
    `{"production": {"alert": 1, "live_activity": 3}, "sandbox": {"alert": 1}}`;
  - `apns_by_environment`: APNs answers since start, e.g.
    `{"production": {"accepted": 12}, "sandbox": {"accepted": 1, "rejected": 1}}`;
  - `registrations_environment_missing` and `registrations_environment_unknown`:
    registrations stored as production because they did not say which environment.
  All of these are counts only; no token is ever shown.
- `POST /api/v1/forge/push/test {"kind": "alert", "title": "…", "body": "…"}` pushes once
  to the caller's own tokens.

## Rotating the key

1. In the Apple Developer portal (team 828357L924), create a new APNs auth key and
   download its `.p8`. The download can be made only once.
2. Put it in `~/.openclaw/secrets/apns/` with mode 600, and move the old `.p8` out of the
   folder: the folder holds exactly one key.
3. Update `push.apns.key_id` and `push.apns.private_key_path` in Thor's config. Tell the
   agent-service lane, which uses the same key for VoIP.
4. Restart Thor's Forge between turns, under the Thor gate (an API restart only; lanes
   keep running). Check `GET /push/status`, then send a test push.
5. Revoke the old key in the portal only after both Forge and the agent-service push with
   the new one.

A key that cannot be read, or is not an EC P-256 key, stops Forge's start with the setting
to fix. There is no silent fallback to running without push.

## Turning it off

- **Everything:** set `push.enabled: false`, or remove the `push:` section, and restart
  Thor's Forge. Registered devices stay in `device_tokens`, unused.
- **One surface:** keep the gateway and set `push.gateway.alerts.enabled`,
  `push.gateway.live_activity.enabled`, `push.gateway.widgets.enabled` or
  `push.gateway.background.enabled` to `false`.
- **Moving the gateway to another node:** turn it off on Thor first, then on the other
  node, so two gateways never push the same notification.

The backup taken before the first enable is
`~/.config/niuu-forge-thor/config.yaml.bak-20261031-push`.

## History

- **2026-10-31 12:26 UTC: enabled** (Thor, `forge-names-39ab05da`), as a config change
  and an API restart; every session was preserved. The first attempt at 12:22 was rolled
  back by its own check, which treated Spark's missing feed as a failure.
- **Test push, 12:27 UTC:** "Forge push is live" to Damien's iPhone. APNs accepted it
  (production, one alert token).
