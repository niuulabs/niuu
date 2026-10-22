# NIU-1140 — accept blocked Tracker Delivery Flow outcomes

- Delivery slug: `a2a-cb22927e64db401dbeb59a7addd02fd5`
- Required base and target: `regin`
- Working branch: `fix/niu-1140-delivery-blocked-outcome`, rebased on `origin/regin` at `fdb97876`

## Tracker

NIU-1140 was resolved and set to In Progress with the repository tracker shim:

```sh
./.skuld-tools/bin/tracker_issue search "NIU-1140"
./.skuld-tools/bin/tracker_issue get "4bbe9dd3-9e29-473e-be59-62a16f556b64"
./.skuld-tools/bin/tracker_issue update-status "4bbe9dd3-9e29-473e-be59-62a16f556b64" "In Progress"
```

The ticket requires a `code.blocked` outcome from `delivery-implement` to be accepted
and sent to a human-resolution gate with the implementer's reason. The `code.changed`
review, merge, and delivery-record publication paths must remain unchanged.

## Review revision

The reviewer found this branch conflicted with `regin`, which already contained the
blocked-outcome implementation in `6ef7b724`. This revision rebases on the current
`regin` and only preserves the unique correction: gate summaries now prefer
`fields.reason` while retaining `fields.rationale` as the compatibility fallback.
