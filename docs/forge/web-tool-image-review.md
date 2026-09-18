# Tool images in the web conversation

Reviewed September 18, 2026. Web branch: `forge/ux-improvement`. Native reference:
Lexi iOS `main` at `9980a1ab`. This is a source review, not a new device test.

## Native behavior and source map

Paths below are relative to the `lexi-ios` repository.

| Source | Behavior reviewed |
| --- | --- |
| `packages/ForgeKit/Sources/ForgeKit/Models/FKImageRead.swift` | Identifies actual image results by `tool_use_id`; reads shallow `is_image`, MIME and dimension hints, full Claude Read envelopes, and retained pre-hint envelope previews. Uses the paired Read path for the filename. Metadata recognition does not decode image bytes. |
| `packages/ForgeKit/Tests/ForgeKitTests/FKImageReadTests.swift` | Covers hinted, full string/dictionary, retained preview and non-image cases. |
| `apps/chat/LexiChat/V2/Views/ForgeImageIndex.swift` | Pairs calls/results in one pass, preserves order, memoizes settled history separately from the changing tail. |
| `apps/chat/LexiChat/V2/Views/ForgeReadImageStrip.swift` | Inline image cards reserve their dimensions, retain aspect ratio, show a filename, offer retry, and open the shared viewer. Native's compact card caps its longest edge at 200pt. Its optional image drawer is off by default. |
| `apps/chat/LexiChat/V2/Views/ForgeImageLoader.swift` | Per-session loader coalesces requests, limits concurrency to three and retains 48 decoded thumbnails with disk caching. Fetches the original when opened. Native also has an automatic full-result fallback when a preview is missing. |
| `apps/chat/LexiChat/V2/Views/ForgeTranscriptView.swift` | Image results become top-level flow items, split tool groups, replace the corresponding Read row and never disappear inside folded execution details. Opens cached thumbnails immediately, then loads full resolution. |
| `apps/chat/LexiChat/V2/Code/ForgeFlowAssembly.swift` | Replaces matched image tool pairs at the call's position, supports orphan image results, and keeps image deliverables outside tool sessions/sheets. |
| `apps/chat/LexiChat/V2/Views/ForgeReadImageFixtures.swift` | Native QA examples for hinted, full, retained-preview and ordinary text results. |

## Web implementation

- Shared `@niuulabs/ui` conversation rendering recognizes Claude Read envelopes,
  Anthropic and MCP image blocks, and Codex data-URL image blocks. Multiple images
  from one tool result each receive a card. Calls and results are paired by ID;
  duplicate deliveries do not duplicate cards. A command mentioning a PNG filename
  without returning image content does not create a speculative file request.
- Cards appear in conversation order between prose and execution groups. As
  requested for web, they are at most **200px high and 400px wide**, retain aspect
  ratio, and do not upscale tiny images. Missing dimensions reserve a stable 4:3
  box and letterbox the preview. Late bytes do not shift surrounding paragraphs.
- Hiding tools is local presentation filtering. The socket continues receiving tool
  results so new images still arrive; room participant/internal-message filters
  remain in place. Claude's user-envelope tool results attach to the assistant,
  including delayed delivery after turn completion, rather than becoming human
  messages.
- The session loader coalesces requests, allows three concurrent thumbnail reads,
  retains up to 48 unused/active cache entries subject to active subscriptions,
  aborts offscreen requests and releases Blob URLs when views close. Only nearby
  cards subscribe. Existing authenticated session routes and remote host prefixes
  are preserved. No new host configuration is needed.
- Opening a card uses Volundr's existing central document overlay and shared image
  viewer: zoom, pan, fit, copy, download and open in a new tab. The cached thumbnail
  appears first; copy/download wait for the original so users do not accidentally
  export a thumbnail. Closing cancels the full-image request and restores focus.
- Failed/invalid thumbnails offer retry and explicit open. Unlike native's automatic
  fallback, the web client never downloads a full image just because a thumbnail
  failed. Thumbnail responses are capped at 2 MiB; explicit full-result/image reads
  are capped at 64 MiB. Supported preview formats are raster images; tool-returned
  SVG is not executed or fetched as active content.
- Native's optional image drawer and custom persistent disk cache are not ported.
  Browser HTTP caching continues to use the server's preview cache headers.

## Backend contract and rollout

The existing route remains compatible with iOS and older web consumers:

```
GET /api/v1/forge/sessions/{session_id}/tool-result/{tool_use_id}/preview
GET /api/v1/forge/sessions/{session_id}/tool-result/{tool_use_id}
```

An optional `image_index` query selects the second or later image. Index zero
retains the existing thumbnail cache key. Shallow results retain legacy first-image
hints and add `image_previews`, an ordered list of metadata for all returned images.
The aggregate proxy forwards the query. Small full results can still be used
without a detail fetch; older full multi-image envelopes are downsampled locally
rather than sending an unsupported index to an old server.

Review found that `CodexWebSocketTransport._dynamic_tool_content_text` flattened
mixed text/image results to text. That permanently removed image bytes before the
broker's image detection or preview API could see them. The transport now retains
mixed content as a JSON envelope; ordinary text output keeps its existing format.
Regression tests cover both emitted results and persisted conversation frames.

**The UI works with the already deployed Claude image contract.** Codex mixed-result
preservation and indexed multi-image thumbnails require the updated backend and
session gateways. Reloading the static UI cannot update an already running Python
transport. Images discarded by an older gateway cannot be reconstructed by this
change. The Thor UI deployment does not restart active gateways or agent sessions.

Saved transcripts use the same renderer. Retained endpoints can supply previews;
otherwise full inline image content can be downsampled locally. A shallow-only
saved transcript without an accessible source reports that limitation rather than
silently returning another file. Standalone brokers without the Forge thumbnail
route likewise report a thumbnail error; explicit open can use their full-result
endpoint.

## Validation

- Backend tests cover typed wire formats, text/non-image rejection, safe metadata,
  Codex mixed-result preservation, indexed preview cache isolation and proxy query
  forwarding. The focused five-file suite passes 282 tests.
- Shared React tests cover flow order, deduplication, hidden tools, live Claude
  result merging, fixed dimensions, request coalescing/concurrency, retry, response
  limits, full-resolution transfer gating, and Blob URL cleanup.
- Playwright exercises inline portrait/landscape cards, stable layout while loading,
  keyboard open/Escape/focus restoration, the existing full-window image viewer,
  zoom, new images while tools are hidden, retry without automatic original fetch,
  and phone viewport containment. These are explicit HTTP/WebSocket fixtures, not
  a claim that an older live Codex runtime now preserves images.
