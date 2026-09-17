# Forge web / Lexi native UX parity

Review: 2026-09-16. Work branch: `forge/ux-improvement`, based on
`origin/forge/dev-integration` at `6512419c`. Native reference: Lexi iOS main
`9980a1ab`. This is a source review, not a new iOS/macOS build or device test.

## Agreed direction

The desktop web client uses the hierarchical conversation presentation: public
explanations remain in order, adjacent tool calls form a disclosure group, and
individual calls expand inside it. Tool calls are visible by default and can be
hidden. There is no selector for the old flat presentation. Leave the separate
folded “worked” block unchanged/deferred. A collapsed subtree must not mount its
expensive detail content. Session list improvements must use real lifecycle APIs.

## Recovered prior work

`origin/lexi/ux-update` (`5be5d0ba`, June 7) contains `93a63039`, the compact
Forge UI, and `11cc4e41`, local-folder Quick Launch. The earlier
`origin/backup/lexi-ux-pre-rebase` retains `eec1886a` (May 29): selected-row
highlight and hover/focus Stop/Archive overlays, and `acee9e38`: tool-call reveal
and cleaner headers. Also present: persisted sidebar width, collapsed groups,
archive visibility and optional metadata. These changes are absent from the
current integration UI. They predate the current Tailwind prefix and native
message identity/interleaving fixes; do not cherry-pick the whole branch.

One behavior must **not** return: the old Archive handler catches a Stop failure
and proceeds anyway. A failed mutation must remain visible; dependent operations
must stop. The old flat/folded conversation renderer is also not this task's target.

Native history confirms `dd8284d9` (hierarchical mode), `603dae75` (typed tool
detail), `5277d7f2` (timed descriptive rows), and `e1d38adb` / `7dd27982`
(desktop disclosures, including subagents/workflows). Current main, rather than
an old proposal alone, is the implementation reference.

## Feature inventory and implementation checklist

`[ ]` means outstanding, not claimed delivered. “Existing” means present in the
web source; it does not imply full native parity. See the iteration receipt below
for implemented and verified items.

| Area                     | Native behavior / evidence                                                   | Web baseline and task                                                                                                                                              |
| ------------------------ | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Session selection        | iPhone list → pushed detail; desktop split-pane; composite host/session keys | Existing deep links and split-pane. [x] Preserve selection through filtering; responsive navigation                                                                |
| Lifecycle vs activity    | `UiBucket`, `FKActivityState`: alive includes idle; working is separate      | Existing activity mapping. [x] Counted Live / Active / Idle / Needs You / Stopped / Error / Archive filters                                                        |
| Row badge                | `SessionStatePill`, elapsed server activity timestamp                        | Dot only. [x] Text badge; keep state readable without relying on color                                                                                             |
| State timing             | Active/idle/waiting duration derives from `activity_state_since`             | [ ] Surface elapsed activity consistently, distinct from last activity                                                                                             |
| Search                   | Name, id, model, workspace; native host chips                                | Existing name/workspace/host search. [x] Compose search with state filtering                                                                                       |
| Grouping                 | Project/repository/status/host/none; remembered; pinned section              | Existing state/repository/Forge. [x] Collapsible remembered groups; [x] project grouping; [x] pinning                                                              |
| Archive visibility       | Lazy archive fan-out; explicit All/Archive                                   | Web store currently fetches both live and archived every listing. [x] Explicit archive visibility; [ ] lazy archived fetch                                         |
| List width               | macOS split view, compact phone list                                         | Fixed 228px. [x] Wider persisted draggable/keyboard-adjustable list                                                                                                |
| Row details              | `SessionDisplayPreferences`: optional details OFF by default                 | Owner may appear as tracker. [x] Clean default; details toggle; meaningful source summary                                                                          |
| Row actions              | iPhone swipe/context; Mac context/hover                                      | Detail toolbar + bulk stopped operations. [x] Row Stop/Archive/Restore/Delete; accessible keyboard/touch affordances                                               |
| Mutation integrity       | Lifecycle API, errors, selection handling                                    | [x] Busy state, visible errors, delete confirmation, refresh relevant caches                                                                                       |
| Rename / pins            | Native name editor and device-local pin set                                  | [x] Web rename and browser-local pins in header/sidebar; pinned group spans state filters                                                                          |
| Launch                   | Native host/model/effort/folder defaults and MRU                             | Existing web catalog/wizard. [ ] Recover local-folder Quick Launch and defaults separately                                                                         |
| Hierarchical transcript  | `ForgeFlowAssembly`, `ForgeTranscriptView`, `MacToolTree`                    | Same-name groups only. [x] Mixed adjacent tool groups, separate call/detail disclosures                                                                            |
| Chronology               | Stable native identities, no grouping across public prose                    | Existing native interleaving fixes. [x] Keep those invariants through new grouping                                                                                 |
| Tool visibility          | `ForgeSessionStore.showToolCalls = true`                                     | Defaults false. [x] Visible by default; persisted toggle; live and replay agree                                                                                    |
| Bounded tool rendering   | Mac pages 25 rows; max six expanded details                                  | [x] Paged expanded groups; [ ] full desktop keyboard/context-menu parity                                                                                           |
| Tool semantics           | Command/read/edit/search/agent/question/generic typed details                | Existing partial Bash/Edit/Read renderers. [ ] Honest running/completed/error states; [ ] native duration/full-output parity                                       |
| Folded work              | Separate preference; current native default false                            | Explicitly deferred by user; no new folded worked block                                                                                                            |
| Markdown files           | `LocalPath.workspaceRelative`, unified preview                               | Browser currently opens host paths as web routes. [x] Workspace and absolute/file links resolve against owning session                                             |
| Documents                | Native text/Markdown/code/PDF/HTML + share/QuickLook                         | Existing text file browser. [x] In-app text/document preview and download; [x] PDF preview; [ ] richer media/QuickLook parity                                      |
| Images                   | Markdown images, tool-result read images, drawer, preview-first viewer       | [x] Markdown image rendering and viewer; [ ] native read-image drawer/cache parity                                                                                 |
| Delivered files          | `FKPresentedFile` for `present_file` / `SendUserFile`, id-based bytes        | [x] Staged `present_file` cards retained when tools are hidden, with authenticated downloads; [ ] native `SendUserFile` result attachments                         |
| Attachments              | Upload/compress, attachment cards, unified preview                           | Existing send attachments. [ ] Incoming/outgoing attachment preview parity                                                                                         |
| Plans / agents           | Live authoritative planes; ordered steps; agent lifecycle                    | Existing mesh/sidebar/gate surfaces differ. [ ] Explicit native plan + agent parity                                                                                |
| Questions / permissions  | Pending controls, reconnect retention, typed responses                       | Existing web permission/question panels. [ ] Full parity review against native cases                                                                               |
| Models / effort          | Runtime capabilities, per-session selection                                  | Existing web controls. [ ] Compare current runtime effort catalog and persistence                                                                                  |
| Terminal / files / diffs | Platform-specific tools, file browser                                        | Existing web tabs; keep working                                                                                                                                    |
| History                  | Bounded recent page, older cursor, projection revision, lazy item fetch      | Web initial broker history currently unbounded. [ ] Implement protocol-v2 paging separately                                                                        |
| Reconnect / sends        | ACKs, request-id receipts, ambiguity recovery, pending inputs                | Web has reconnect and text repair. [ ] End-to-end native recovery/steering matrix                                                                                  |
| Cache                    | Native inactive-model LRU, optional disk prefix, lazy tool/image caches      | TanStack + adapter caches and broker-keyed tab `sessionStorage` snapshots; no native canonical disk-prefix/LRU equivalent. [ ] Cache design and invalidation tests |
| Scroll discipline        | Native no unsolicited jumps; explicit older/jump-to-bottom                   | Existing web follows near bottom. [ ] Verify expansion/replay never pulls reader away                                                                              |
| Accessibility            | Native platform controls; Mac keyboard disclosures                           | [ ] Web focus, labels, touch targets, reduced motion and resizer keys                                                                                              |

## ForgeKit and native data flow

The shared kit owns wire types and transport, while the app owns transcript
presentation and most heavy caching. These are separate responsibilities:

1. `ForgeConfigLoader` and `ForgeInstanceConfig` resolve configured hosts.
   `ForgeInstancePool` manages clients, health/circuit state and capability data.
   `ForgeMesh` in `packages/ForgeKit/Sources/ForgeKit/ForgeKit.swift` fans out
   listings, keeps per-host failures visible, uses composite identities and
   coalesces overlapping relist requests. Archived history is a sticky explicit
   opt-in, fetched as an additional bucket rather than silently omitted.
2. `ForgeHTTPService` implements sessions, create/name/lifecycle, project
   discovery/connect/register, conversations/pages/turns, lazy tool results and
   previews, events, message delivery receipts, models/features and chronicles.
   Mutations are not blindly retried. GET policy: 15s request timeout, two retries,
   250ms initial delay ×2. Base path is `/api/v1/forge`, supplied host/config first.
3. `FleetStream` supplies list/activity updates. The constants define a 45s
   silence threshold, health checks every 10s, and three failed SSE attempts before
   an explicitly reported poll mode, with a 60s SSE recovery probe. Kit comments
   are not proof that the web has the same policy.
4. `SessionSocket` owns the per-session stream, commands, ACKs, reconnect,
   visibility reassertion and outbound buffering. `FKReconnectTracker` uses 1s
   exponential delay, 30s cap, 30% jitter, ten retries, and resets after five seconds
   of stable uptime. Constants bound outbound buffers to 100 messages / 30s TTL
   and websocket messages to 16 MiB. No UI parity work may resend uncertain input.
5. `CodeSessionLiveModel` merges canonical history with live native fragments,
   pending steering and optimistic echoes. It fences stale REST loads and keeps
   server indices separate from local display rows. Reconciliation polls at
   1.5s while busy and 4s while idle. Current native reads bound recent history to
   15 rows / 256 KiB and track projection revision, older/refresh cursors and
   explicit oversized-item recovery. Plans/agents have their own live updates
   plus reconciliation; history is not evidence that an agent is still alive.
6. `ForgeSessionStore` reuses models by composite key, keeps three inactive
   histories by default, trims on memory pressure and preserves unsaved work.
   `ForgeSessionDiskCache` is optional (default on), schema v4, serial off-main
   I/O, atomic files under Caches, at most 32 MiB per snapshot. Only a proven
   settled canonical prefix is admissible: pending steering, optimistic echoes
   and any mutable native span stop that prefix. A changed projection revision
   invalidates it; new opens still fetch fresh bounded membership.
7. Tool inputs/results are fetched on demand by tool-use id and cached per
   session. `ForgeImageLoader` is preview-first, bounded to three concurrent
   requests and 48 entries, with session-scoped preview files; full image loads
   happen on demand. This must not be approximated by embedding all result bytes
   in every row.
8. `ForgeFileService` resolves session workspace links, downloads regular files
   and id-keyed presented files, and caps inline text at 2 MiB. The server owns
   allowed-path validation. Native `SendUserFile` is a distinct carrier: the paired
   result contains attachment UUIDs; retrieval uses
   `/s/{session}/api/conversation/tool-result/{toolUseId}/files/{uuid}`. Do not
   send these UUIDs to the staged `present_file` registry route. `ForgeTranscriptView.handleFileTap` routes local
   files and recognized remote media to preview; ordinary web links retain normal
   navigation. Image URLs are resolved through that same owning-session boundary.

## Additional native surfaces tracked for subsequent iterations

- [ ] Voice review overlay, dictation ownership/send reveal and voice-call transcript links
      (`CodeSessionDetailView`, `ForgeVoiceReviewOverlay`).
- [ ] Steer bar, runtime commands, model/effort settings, pending-send recovery and
      user-question/permission cases (`CodeSessionDetailView`, `CodeSessionLiveModel`).
- [ ] Agent lens and plan dock, native parent/subagent/workflow nesting, activity duration,
      bounded open tool details and context menus (`ForgeTranscriptView`, `MacToolTree`).
- [ ] Native image-result drawer, full-resolution preview-first cache, attachment sharing,
      read-image blocks and native `SendUserFile` result/UUID delivery (`FKPresentedFile`,
      `ForgeFileService`, `ForgeImageLoader`). Staged `present_file` is implemented below.
- [ ] List pinning/rename, project grouping and local-folder Quick Launch; restore the
      recovered prior UX selectively against the current runtime catalog.

## Implementation boundaries

Work is in the Niuu web worktree only. Native main and other active UX worktrees
are reference inputs, not merge targets. Keep provider/native transcript identity
and original tool/result bytes. Do not replace Thor's active backend or restart
running sessions to validate a UI. Unit/browser fixtures exercise destructive
controls; live validation is read-only. The existing Tailscale review deployment
can serve a separately built web release when checks pass.

## Iteration receipt

First iteration, 2026-09-16:

- The session list defaults to Live, includes state counts and badges, hides low-value
  metadata, remembers grouping/filter/details/width, and collapses groups. Width starts
  at 340px and clamps to 280–640px; drag, arrow keys, Home/End and double-click reset work.
- Stop/Archive/Restore/Delete appear on hover/focus and remain touch accessible. Delete
  requires confirmation. Stop failures prevent Archive; errors remain visible. Existing
  bulk stopped actions retain failed selections after partial deletion.
- Narrow screens use list → detail navigation with a Sessions back button. Desktop
  retains a split view. Filtering does not navigate away from the current session.
- Adjacent mixed tools form hierarchical disclosure groups without crossing prose.
  Groups page 25 calls; closed details are unmounted. Tools default on and the saved
  preference is reasserted on connection. Existing settled/live text anchors are retained.
- Workspace/file links use the injected filesystem port. Text/Markdown, images and PDF
  preview in a dialog; all bytes can be downloaded. Document-relative links stay relative
  to the open document. Inline text is bounded to 2 MiB, pending loads abort on close,
  object URLs are revoked, and failures are displayed. HTML is shown as source text.
- Staged `present_file` cards stay visible with tools hidden. A native `SendUserFile`
  lacking a staged id currently displays incomplete delivery; full paired-result support
  remains an explicit follow-up, not a claim of native attachment parity.

Validation: production build, focused adapter/component tests, the full 6,076-test unit
suite with unchanged 85% coverage thresholds (92.92% statements, 85.29% branches,
92.32% functions, 94.49% lines), and 21 passing Playwright fixture checks.
The browser checks cover disclosure/order, reload/selection, filter/resize persistence,
mutation errors/cancel, previews/loading/error, and narrow-screen navigation. Live Thor
checks are read-only; the UI serves the branch build through Tailscale on port 3001 and
proxies its API/WebSocket calls to localhost:8080. The active backend is not restarted.

Full native transport, cache, voice and project-coordination parity is not implied by
this first UI iteration. Review the unchecked items above to choose the next slice.

## xTeo, Markdown and document viewer iteration — 2026-09-16

References reviewed:

- LexiDesignKit `DesignTokens.swift`, `GlassCardSurface.swift`, and native
  `MarkdownView.swift`, `FilePreview.swift`, `CodeFilePreviewSheet.swift`,
  `FileClassification.swift` at the native revision above.
- Lexi web `packages/markdown` and `apps/lexi-chat/src/components/file-viewer`
  at `552b770`. The shared React renderer adopts its CommonMark/GFM pipeline,
  path-linkification, callouts, highlighted code, math and Mermaid behavior.
  Styles and file access remain Niuu token/port based.
- User-supplied `Web UI refinement mockup (1).zip`, SHA-256
  `ed4127a780d445ee231e9c42bc9321f9759d61e62bc9804b14fdb0f72bd0ab21`.
  Extracted and visually rendered `Volundr Web Refined.dc.html`; reviewed both
  included screenshots. Adopted its stronger sans-serif hierarchy, compact
  session controls, spacious transcript, quieter metadata, bordered tool cards,
  raised composer and blue accents. The native SF/Inter stack stays consistent
  with Lexi; the mockup's fixed 1400px minimum becomes a responsive layout.
  Its sample session counts/messages remain reference content, not application
  data. The explicit request to omit the total Sessions heading count prevails.

Delivered:

- [x] Shared `xteo` theme: native navy surfaces, deeper blue filled controls,
      brighter secondary/faint text, readable semantic tool/status accents,
      stronger headings, subtle elevation and borders throughout the shell.
- [x] Always available top-bar theme selector: xTeo blue / Native dark, plus the
      existing Amber/Spring options. Preference persists in `niuu.theme`; initial
      selection comes from runtime configuration. Portal previews inherit it too.
- [x] Sidebar heading is just “Sessions”. Filter counts stay useful; wider controls,
      balanced spacing and a separate action tray prevent buttons covering row text.
- [x] Phone top bar places plugin navigation on its own row, keeping the theme
      switch inside the viewport. Session navigation and toolbar wrap independently.
- [x] Shared TypeScript React Markdown in live and settled Claude Code/Codex Forge
      presentation, with stable DOM anchors through streaming. Proper nested/loose
      lists, tasks, strike/emphasis, references, autolinks, aligned tables, callouts,
      heading links, inline/fenced code, native MathML and Mermaid.
- [x] Memoized Markdown components; deferred syntax work with a bounded 40-entry
      cache and a 100,000-character highlighting ceiling. Code remains readable
      while highlighting loads. Mermaid waits for settled source and bounds input
      at 50,000 characters; unavailable diagrams show their original source.
- [x] Fenced outcome cards and archived summary cards remain supported; examples
      of outcome markers inside ordinary code remain literal.
- [x] Workspace-relative, absolute-within-workspace, `file://`, bare and backticked
      file references use the owning session. Tool file paths and Files-browser
      preview actions open the same viewer. Document-relative links stay relative.
- [x] Viewport-sized, keyboard-dismissable document viewer: filename/path, kind/size,
      Preview/Source, copy text/path and download; rich Markdown, highlighted source,
      Mermaid, images with zoom/fit, PDF, browser-supported audio/video and HTML.
- [x] HTML uses a sandbox without scripts or same-origin privileges and a restrictive
      document CSP; raw HTML is disabled in Markdown. Mermaid uses strict mode and
      sanitizes SVG before displaying it as an image. Link schemes stay constrained.
- [x] External Markdown images open in a viewport dialog with an original-image link.
- [x] Loads abort on close/change; blob URLs are revoked. Text previews stay bounded
      at 2 MiB. Larger/unsupported files have an explicit download path; unavailable
      media and request failures are visible. HTML preview supports self-contained
      documents; it does not load arbitrary scripts or external resources.

Remaining native parity stays explicit: incoming `SendUserFile` attachment UUIDs,
read-image result drawers and preview-first image caches, office/archive QuickLook
formats, activity durations and richer tool failure/timing summaries. The mockup's
example duration/failure badges are not invented when the current transcript
projection does not carry those fields.

Validation: full unit suite and coverage gate, package/app type checks, lint and
production build; focused streaming/Markdown/link/security/classification tests;
browser checks for actual diagram rendering, highlighted prose/code, theme reload,
preview/source switching, image zoom, and desktop/phone layout. Live deployment
continues to proxy Thor `127.0.0.1:8080`; no backend restart or test-session mutation.

Final iteration receipt: 6,114 tests in 415 files pass. Coverage: 92.89% statements,
85.23% branches, 92.23% functions, 94.44% lines. All 23 Forge browser regressions
pass; the five theme/preview/layout checks also pass after the final phone CSS
correction. Production build, type checks and lint pass. The xTeo foreground ramp
is at least 5.04:1 against all five primary/secondary/tertiary/elevated/sent
surfaces; white on the deep-blue action fill is 5.17:1.

## Quick launch and Forge connections — 2026-09-17

Reviewed Lexi iOS `NewForgeSessionView.swift`, `ForgeEffortProfile.swift`,
`ForgeHostStore.swift`, `ForgeSettingsView.swift`, and ForgeKit's registry loaders.
The iOS creation contract uses a selected host, explicit session definition/model,
`source.local_path` plus a `/workspace` mount, and `workload_config.reasoningEffort`.
Its working folder is host-specific, effort prefers `xhigh`, and models are curated.

Implemented in the web UI:

- [x] Catalogue and dashboard expose exactly two quick-launch standards: Claude
      (`skuldClaudeInteractive`, interactive tmux) and Codex (`skuldCodex`, Skuld CLI).
- [x] Defaults: `claude-fable-5-1` and `gpt-6-astra`. Explicit alternatives are
      `claude-opus-5` and `gpt-5.6-sol`; availability comes from the connected Niuu
      Bifrost catalogue, not an invented model list. Opus 5 is currently unadvertised
      on all four hosts, so its option is disabled rather than replaced with Opus 4.8.
- [x] Preserve Bifrost effort metadata, including an explicitly empty list; remember
      effort per model in this browser. Prefer Extra High, then the advertised default.
- [x] Direct create, without a review/confirmation step or saved-preset mutation.
      CPU/memory/GPU, credentials, MCP and rules are optional and omitted in quick launch.
- [x] Local mount by default. Absolute working folder, remembered separately per host;
      configured folder used for a host without a remembered folder. Explicit Git mode
      retains repository and branch selection. Name and initial prompt are optional.
- [x] Advanced launch and custom catalogue preserve the existing full editor,
      tracker issue selection, resource controls, integrations and saved launch specs.
- [x] Connection management lives in Guild (`/guild`), using its existing environment
      registry and registration flow. Quick Launch links there and reads enabled targets.
      The duplicate Forge Hosts tab and its editor/service methods have been removed.
- [x] Tests cover create payloads, resource omission, effort controls, unavailable
      models, per-host folders, duplicate submission, Git mode, failed create, registry
      CRUD/probes, preserving embedded transport, advanced access, and iPhone width.

### Where configuration lives

The web app calls `/api/v1/niuu/instances?kind=volundr`. This shared server registry
is also used by Guild and the `/api/v1/forge` session facade; it is not a separate
browser list. Creation sends the selected registry UUID as `instance_id`.
`config.defaultFolder` belongs to each registry host. Browser preferences under
`niuu.forge.launch.*` hold the last selected host, successful folder, and effort.
Changing the selected Forge does not change the web frontend's own server URL.

Lexi iOS has the same four bundled hosts in
`apps/chat/LexiChat/V2/Model/ForgeHostStore.swift`. Device-owned overrides are in
UserDefaults (`lexichat.forge.hosts.v1`); this Linux workspace cannot read a phone's
private overrides or Keychain. The matching addresses were resolved and each
host's health and model catalogue checked directly:

| Label     | Forge origin                 | Initial working folder  |
| --------- | ---------------------------- | ----------------------- |
| Thor      | `http://100.66.123.128:8080` | `/home/thor/repos/niuu` |
| Spark     | `http://100.127.141.74:8080` | `/home/xteo/repos`      |
| Build     | `http://100.81.183.4:8080`   | `/home/horde`           |
| Build Bro | `http://100.115.8.110:8080`  | `/home/horde`           |

Thor retains its existing registry UUID, slug `local`, default status, and
`config.transport=embedded` to avoid routing the local aggregate into itself.
The four connections are owned by the existing local `dev-user`, allowing the
normal browser identity to edit them. Its visible origin is an ordinary IP URL. The web frontend's `/api` and local
session proxy still point to `127.0.0.1:8080` on Thor. Names are display labels.

Deployment bootstrap configuration is
`/home/thor/.config/niuu-forge-thor/config.yaml` (`niuu.instances`); configured seed
values are reapplied when the backend starts. The shared registry stores UI edits
between reloads. If changing a seeded host permanently, update its bootstrap
entry too. Non-seeded hosts created in the UI remain in the database.
The UI's `/config.json` controls service base URLs, not the host list.

The private HTTPS UI uses nginx in
`/home/thor/.config/niuu-forge-web/nginx.conf`. The four known HTTP Forge session
sockets are rewritten to same-origin secure proxy routes. A newly registered HTTP
host also needs such a session proxy (or an HTTPS Forge endpoint); saving its REST
origin alone cannot bypass a browser's mixed-content restrictions.

Remaining differences from iOS: on-device overrides are not automatically synced;
model/definition choices use the connected Niuu catalogue rather than a separate
capability fetch from each host; project assignment, worktree creation and a native
folder history menu remain outside this quick-launch change. No real provider
session was created for testing.

## Session sidebar and preview refinement — 2026-09-17

- [x] Filter order: Live, Active, Idle, Needs you; a subtle divider; Stopped, Errors,
      All, Archived. No extra filter colours. The Sessions heading owns the bold launch
      plus and CLI import action; the launch tooltip appears after 100 ms.
- [x] Full-width search; details and token preferences in a persistent bottom bar.
      Stopped-session selection and archive controls stay visible in that footer, without
      an extra disclosure. Selecting all stopped does not delete anything; deletion still
      requires the existing confirmation.
- [x] CLI import search matches names, IDs, folders, harnesses and models.
- [x] Project grouping reads the existing Forge `/projects` endpoint and session
      `coordination` metadata. Registered replicas share a project group. Parent/child
      sessions form a collapsible tree; filtered parents, missing parents and cycles never
      hide a session. Unassigned sessions remain under No project. State/Repo/Forge
      grouping remains available. Project fetch failures preserve the session list.
- [x] Two-line entries: name + coloured state; Claude/Codex + source icon/path or Git
      repository/branch + activity age. Claude orange and Codex blue remain constant
      across themes. Recorded harness takes precedence over legacy model inference.
      Active is green, attention amber, errors red, idle/stopped neutral, matching the
      native status vocabulary. The hover/focus action tray overlays the state beside the
      name with an opaque background; touch users get an explicit actions button.
- [x] Token usage is off by default, persisted browser-locally, and shared by live and
      replay views. Counts round compactly, e.g. `150k → 234 tokens`.
- [x] Document and image dialogs use 90vw × 90dvh (about 81% of window area), with
      no fixed pixel cap; phone previews keep only a narrow viewport margin.
- [x] Workspace and HTTP(S) image hyperlinks get lazy hover/focus thumbnails and
      open the in-app image viewer. Inline Markdown images use the same viewer. At this iteration, normal
      website links retained normal navigation (superseded by the next receipt). Image links are recognized by the filename
      extension (query strings ignored) or the workspace resource's image MIME type.
- [x] Image viewer supports zoom buttons, wheel/pinch zoom, drag panning, keyboard
      panning/zoom, fit/reset, image copying, downloading, and opening the original in a
      new tab. Workspace image bytes stay behind the authenticated resource port. Remote
      transfers omit credentials and require the remote host's CORS permission; failures
      are shown in the viewer, without redirecting or claiming a copy/download succeeded.

Validation: full production rebuild (including dependent plugins, to remove old bundled
styles); all workspace type checks; changed-file lint; 6,142 tests in 418 files pass.
Coverage: 92.66% statements, 85.07% branches, 91.99% functions, 94.21% lines.
Browser checks cover quick launch, Guild link, sidebar ordering, project disclosure,
persistent settings, CLI search, responsive document sizing, image hover/focus,
real clipboard PNG writes and downloads, panning, native-dark/xTeo harness colours,
desktop action alignment and phone layouts. All 13 browser checks pass, including
touch menu activation without triggering an underlying action. All mutations in browser tests use fixtures;
no real provider session is launched, stopped, archived, imported or deleted.

## Session links, header and control alignment — 2026-09-17

- [x] Markdown HTTP(S), email and file links show their resolved target after a
      150 ms hover/focus delay. Workspace resources, remote images, remote Markdown
      and websites open in the session's central 90vw × 90dvh preview. Remote Markdown
      uses the shared renderer and resolves nested links against its URL. Fragment links
      remain document navigation; unsafe schemes and paths outside the workspace remain
      unavailable. Opening another document replaces the panel rather than stacking it.
- [x] Remote websites use a sandboxed iframe, with explicit Copy link and Open in new
      tab controls. Sites can refuse embedding; the panel explains this limitation.
      Remote Markdown downloads omit credentials, need CORS permission, abort on close,
      revoke object URLs, and have the same 2 MiB text-preview limit as workspace files.
      Copy and download failures remain visible. Closing the session preview restores
      keyboard focus to its original link.
- [x] The workspace chip expands into folder, available repository/branch details and
      a session-ID footer, with copy controls. Repository links use the same preview.
      Local-mount session metadata currently contains a folder, not a Git remote or
      branch: this absence is stated explicitly, not inferred from the folder name.
- [x] The header includes the selected model's readable name, preserving unknown IDs.
      The redundant Hierarchical label is removed; rendering remains hierarchical.
- [x] Hiding tools replaces each adjacent run with one thin, padded separator and
      discards its displayed inputs/results. Native prose identities and delivered file
      cards are retained in settled, streaming and room views.
- [x] The two filter rows use aligned, equally sized controls. Grouping uses a centred
      label and four equal segments. Launch/import/collapse have matching rounded
      borders; the plus is smaller and lighter. Sidebar and header Stop/Delete use
      the same red tokens; Stop has an outlined resting state and solid red hover.
      The explicit touch menu remains isolated from destructive actions.

Validation: full production rebuild, workspace type checks, changed-file lint;
6,164 unit tests in 421 files, 85.14% branch coverage (other coverage metrics above
92%), and 15 browser checks. Browser coverage includes desktop and phone layouts,
both themes, website/Markdown/image previews, target tooltips, nested link resolution,
keyboard focus return, hidden-tool boundaries and action colours. Lifecycle operations
remain fixture-only; the live deployment check is read-only.

## Pinned sessions — September 17

The header and row action overlay have a pin next to the rename pencil. Pinned
sessions appear once in a collapsible Pinned section above every State, Repo,
Forge or Project group, regardless of their current state or the selected state
filter. Pin order is stable (the order they were pinned). Search still narrows
pinned and ordinary rows. Unpinning returns the session to its normal group;
unpinned project descendants remain accessible when their parent is pinned.

Pins and the collapsed state are remembered in this browser and synchronize
between open tabs of the same origin. This is personal presentation state, not a
Forge session mutation or cross-device account synchronization. Pins remain saved
if a session is temporarily unavailable; missing/deleted sessions produce no row.
State-filter counts and stopped-session lifecycle actions retain their existing
meaning.
