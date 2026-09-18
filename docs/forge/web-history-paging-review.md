# Web conversation history paging

The web chat requested a complete WebSocket replay while also loading complete REST history. A long session exceeded the socket replay limit; the generic error handler then rendered `conversation_history_too_large` as an assistant failure. Depending on REST/socket timing, that warning could disappear or remain in the conversation.

## Implemented behavior

- The shared Skuld chat reader loads the newest 50 turns through REST. Scrolling upward or selecting **Load earlier messages** loads another 50. Shorter conversations stop at their beginning. The reader holds a visible message anchor when prepending rows.
- Each automatic REST response is capped at 256 KiB. A 50-turn batch may require several smaller requests because gateway limits and large messages can reduce an individual page. Oversized responses are rejected during streaming, before JSON decoding.
- Session URLs retain their owning host/proxy prefix. The Forge conversation facade pages both current and retained older gateways. Current servers use opaque cursors; older servers use `before` with an overlapping message ID/index check and bounded retries when appends move the tail. A changed projection requires a recent read rather than silently stitching unrelated pages.
- WebSockets request `history=recent&history_protocol=2&history_delivery=none`. Current gateways stream live events only; retained gateways may supply a recent snapshot, which cannot replace the REST window. A read after socket attachment closes the initial-read/connection gap. Reconnect refreshes recent history and retains loaded older rows when the windows overlap in the same projection.
- Typed replay errors and `history_gap` request coalesced REST recovery. They never become messages or terminal agent failures and never resend user input. Ordinary errors and ordinary text quoting the warning remain visible. Failed initial, older, and recovery reads remain retryable. Session changes cancel pending reads.
- Large tool input/output is fetched from the owning session only when its tool card opens. A truncated message is explicitly marked as a preview and opens a separate full-item reader. Metadata-only previews do not invent an author. These explicit expansions are separate from automatic bounded history.
- The cached transcript is limited to the latest 50 messages. Loading older pages does not turn the next initial view into an unbounded cache replay.

## Compatibility and scope

This change covers shared web Skuld chat in Forge and Ravn. Native socket-only consumers retain their own history contract. Existing stopped-session archive loading is separate from this live-session reader. Old server internals may still materialize their full history before the facade windows the response; this browser change bounds browser traffic and rendering, not all work inside those retained servers. Unsupported full-item APIs report an explicit expansion error.

No backend restart, provider request, or user-message resend is part of deployment. The server integration branch remains `forge/dev-integration`; this UI change belongs to `forge/ux-improvement`.

## Validation

Regression coverage includes initial/older/final pages on both protocols, appends moving the legacy seam, huge seam rows, ignored cursors/budgets, typed conflict handling, reconnect retention, coalesced recovery during initial load, live-token races, cancellation/session switches, retryable reads, and lazy tool/message expansion. Browser checks measure the same visible message before and after prepending on both protocols, plus manual phone paging and failure/recovery flows.

Read-only staging checks on September 18 verified live sessions on Thor, Build, and Build Bro. The 299-turn lexi-coordinator session opened with 50 messages and reached 100 after scrolling upward. All observed history responses honored the 256-KiB budget. Outbound socket controls and non-read HTTP methods were blocked by the test browser; no test input was sent to these sessions.
