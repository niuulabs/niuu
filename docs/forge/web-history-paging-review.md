# Web conversation history paging

The web chat requested a complete WebSocket replay while also loading complete REST history. A long session exceeded the socket replay limit; the generic error handler then rendered `conversation_history_too_large` as an assistant failure. Depending on REST/socket timing, that warning could disappear or remain in the conversation.

## Implemented behavior

- The shared Skuld chat reader requests up to the newest 10 turns in one REST page. Scrolling upward or selecting **Load earlier messages** requests up to another 10. Shorter conversations stop at their beginning. The reader holds a visible message anchor when prepending rows.
- Each history-page response is capped at 256 KiB. Requests reserve 4 KiB for routing/timing metadata that retained facades append after fitting the page. Gateway limits and large messages can reduce an individual page. The client displays that smaller page immediately and does not fetch older pages to fill the requested count. Oversized responses are rejected during streaming, before JSON decoding.
- Session URLs retain their owning host/proxy prefix. The Forge conversation facade pages both current and retained older gateways. Current servers use opaque cursors; older servers use `before` with an overlapping message ID/index check and bounded retries when appends move the tail. A changed projection requires a recent read rather than silently stitching unrelated pages.
- WebSockets request `history=recent&history_protocol=2&history_delivery=none`. Current gateways stream live events only; retained gateways may supply a recent snapshot, which cannot replace the REST window. A read after socket attachment closes the initial-read/connection gap. The single initial page displays as soon as it arrives; a queued post-attachment read then reconciles in the background, without cancelling/repeating the foreground request or holding the loading screen. Reconnect refreshes recent history and retains loaded older rows when the windows overlap in the same projection.
- Typed replay errors and `history_gap` request coalesced REST recovery. They never become messages or terminal agent failures and never resend user input. Ordinary errors and ordinary text quoting the warning remain visible. Failed initial, older, and recovery reads remain retryable. Session changes cancel pending reads.
- Large tool input/output is fetched from the owning session only when its tool card opens. When a history page truncates a message, the client automatically reads that exact turn before rendering the batch. It requests shallow tool detail but complete prose, so the message uses the ordinary inline Markdown and streaming renderer, without a preview card, open button or modal. These per-message reads are not capped by the page byte budget. Retained facades that ignore `turn_id` may return a whole shallow transcript; the reader selects exactly one matching identity and rejects missing/duplicate identities. Metadata-only rows are replaced with the actual message and author. Reads are sequential and share the batch cancellation signal; failures use the normal history retry flow.
- The cached transcript is limited to the latest 10 messages. Loading older pages does not turn the next initial view into an unbounded cache replay.

## Compatibility and scope

This change covers shared web Skuld chat in Forge and Ravn. Native socket-only consumers retain their own history contract. Existing stopped-session archive loading is separate from this live-session reader. Old server internals may still materialize their full history before the facade windows the response; paging limits the number of rendered messages, not the size of an individual complete message or all work inside those retained servers. Unsupported full-item APIs report an explicit expansion error.

No backend restart, provider request, or user-message resend is part of deployment. The server integration branch remains `forge/dev-integration`; this UI change belongs to `forge/ux-improvement`.

## Validation

Regression coverage includes initial/older/final pages on both protocols, appends moving the legacy seam, huge seam rows, ignored cursors/budgets, typed conflict handling, reconnect retention, coalesced recovery during initial load, live-token races, cancellation/session switches, retryable reads, lazy tool expansion, automatic full-message reads, and live events arriving during those reads. Browser checks measure the same visible message before and after prepending on both protocols, plus manual phone paging and failure/recovery flows.

Read-only staging checks on September 18 verified live sessions on Thor, Build, and Build Bro. The 299-turn lexi-coordinator session opened with 50 messages and reached 100 after scrolling upward. All observed history responses honored the 256-KiB budget. Outbound socket controls and non-read HTTP methods were blocked by the test browser; no test input was sent to these sessions.

## Active-turn replay regression (September 18)

The `build-storage-spike` gateway retained the earlier conversation. Its first
bounded REST response contained only a preview of a very large in-progress turn;
older turns were available through additional pages. Three web behaviors obscured
that data:

1. A live event during a history GET caused the client to discard **every
   in-progress REST turn**, including commentary and tools produced before the
   browser connected. The old test incorrectly required this loss. The client now
   binds native turn/item/tool identities, restores canonical earlier parts,
   preserves newer live completions/results, and updates the streaming references
   so the next event cannot erase the recovered prefix. Distinct turns and threads
   are not joined by text, timestamps or status. A turn completed while the GET
   was pending remains completed.
2. Pre-attachment loading completed the whole batch and then repeated it after
   socket attachment. The September 18 repair cancelled that large batch. With
   the September 19 single-page window, the initial page instead finishes and
   displays immediately; the post-attachment read catches up in the background.
3. Retained facades can append metadata after fitting the requested byte budget.
   The browser now requests slightly less than its hard receive limit. The large
   preview also no longer traps Markdown inside a 12-rem nested scroller, and its
   full-message reader accepts the retained facade's exact-ID transcript response.

The September 19 refinement removes the preview presentation. A truncated row
is automatically resolved before the history/live merge, so its complete part
sequence can seed streaming and merge with new activity in one normal message.
The incremental paging and page byte budget remain; individual message reads may
exceed that budget. Tool bodies retain their lazy expansion behavior.

Regression tests exercise both paging protocols with socket events during REST
loading, subsequent live updates, completion during loading, oversized previews,
legacy full-item expansion and extra facade metadata. The read-only staging
browser also opened the real Build session and its full-message reader without
provider input or outbound socket controls. Deployment requires static UI files
only; it does not require an API or gateway restart.

## Smaller activation window (September 19)

Opening `build-storage-spike` on the deployed UI took 17.3 seconds in a clean
browser and 17.7 seconds on reload. The client started 19 history requests to
assemble 50 turns, including reads for older oversized messages. That work was
unnecessary for entering the current conversation.

The reader now requests one page of up to 10 turns and accepts a smaller page
without chasing earlier history. Upward scroll/manual paging requests the next
page with the same 10-turn limit. Legacy seam validation, complete inline message
loading, live-event reconciliation and cancellation remain. Previously loaded
older pages stay available while the conversation is open; reopening restores
only the small recent cache.

With candidate assets against the same live APIs, the first view took 1.71 seconds,
reload 1.62 seconds and a channel switch 0.86 seconds. Each needed one history
request and displayed the available latest two turns. These are measured runs,
not a guarantee for every host or network. The 256-KiB page ceiling remains: lowering
it would turn complete large messages into transport previews and force additional
full-message requests, increasing transfer and delay. No older page is requested
until the user scrolls up or presses Load earlier messages.

Regression tests cover the 10-turn limit, accepting a smaller byte-limited page,
continuous legacy boundaries when appends shorten a page, reaching the earliest
message without gaps, anchored upward scrolling, reconnects and live streaming.

Post-publication timing exposed a second delay: socket attachment cancelled the
first request and restarted the same remote history build. The initial page now
finishes and renders before the queued post-attachment refresh. That refresh still
closes the REST/WebSocket connection gap, merges live events and reports failures;
it does not block the first page or discard it on a failed catch-up.
