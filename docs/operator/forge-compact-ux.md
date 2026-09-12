# Compact Forge and shared Niuu chat

This integration adapts the work from xteo/niuu `dev-integration` at
`11cc4e41` to current Niuu, including full and multi-host deployments.

## Launch

The Sessions add button and Forge's custom launch open a compact dialog. On a
mini-mode host it initially offers a local folder; on a full host it initially
offers a Git repository and optional branch. Available engines and their model
defaults come from the configured session definitions. Local folders are only
available when the selected Forge enables local mounts. Paths refer to the
Forge host, not the browser's machine.

Choose a Forge when more than one is available. Capabilities are fetched for
that target, not inferred from the default host. Failed capability or catalog
requests remain visible and prevent launching with guessed settings.

An optional name is derived from the folder or repository when omitted. An
optional instruction starts the session. Advanced launch retains the existing
resource, credential, integration, preset, workspace and routing controls.
Launching a catalog spec continues to use that spec's advanced flow.

## Sessions and chat

- Session groups fold; stopped sessions begin folded and archives begin hidden.
  The archive toggle reveals archived groups. Bulk selection unfolds stopped
  sessions so the selected rows stay accessible.
- Session rows offer stop and archive on hover or keyboard focus. Touch devices
  show the controls. A failed stop prevents the following archive and displays
  the error.
- Drag the divider to resize the session list, or focus it and use arrow keys,
  Home or End. Width, group folds and archive visibility persist locally.
- The Details button toggles technical Forge metadata in session rows and the
  session header. Costs and participant identity remain visible.
- Shared session chat defaults to compact turns: prompt, expandable work, final
  answer. Expanded view remains available; the internal-events control also
  expands work. Error messages are not hidden inside collapsed work. Room and
  thread grouping preserve participant attribution rather than choosing one
  participant's message as the whole room's answer.
- The Display menu controls assistant avatars, action rows, timestamps and copy
  placement. Copy remains accessible with action rows hidden. These shared
  components also serve Ravn and other Niuu consumers, including room messages.
- Conversation history restores image thumbnails from structured content blocks
  and embedded base64. Code examples and unknown structured blocks are preserved.

Preferences use the original `niuu.compactUx.*` browser-storage keys.

## Modules and development transport

Plugin JavaScript and styles load according to existing runtime
`config.json` plugin enablement. All current platform modules remain available;
operators can configure a Forge-focused installation without rebuilding or
editing an allowlist in source.

The Vite development server proxies `/s/` HTTP and WebSocket requests to
`NIUU_API_PROXY_TARGET`, like API requests. Relative endpoints and advertised
loopback session endpoints use the browser origin; intentionally remote
endpoints retain their host. `/s/` includes the trailing slash so it cannot
capture Vite's `/src` requests. To bind another interface set `NIUU_DEV_HOST`;
use Vite's supported additional-host setting
`__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS` for a development hostname.

## Earlier work preserved

The source branch's model defaults, native conversation resume, initial-prompt
suppression, and resume route correction were already integrated or superseded
on dev (notably PR #778). This branch preserves those implementations, current
model-aware effort handling, concurrency settings, the Forge/runtime versus
Volundr/catalog split, and migration 000046. It does not introduce the source's
colliding migration 000043 or replace runtime defaults with older values.

## Integration verification

Validated on the integration worktree:

- Frontend: 415 test files, 6,072 tests passed. Coverage: 92.80% statements,
  85.11% branches, 92.05% functions, 94.36% lines; all 85% gates passed.
- Gateway: all 53 tests in `tests/test_niuu/test_rest_volundr.py` passed,
  including selected-target flags and inaccessible-target rejection.
- Browser: five Playwright checks passed for local and Git launch, capability
  loading/failure, keyboard resizing, metadata controls, and compact history
  with display preferences inside Forge. These use explicit HTTP/WebSocket
  fixtures; they do not launch a real model process or Kubernetes workload.
- Workspace typecheck, production app build, modified-file ESLint/Prettier,
  Python Ruff and Git whitespace checks passed. Build output retains the
  existing advisory about large chunks; Node 25 emits its localStorage warning
  during tests.

The changes are prepared on `codex/forge-ux-integration`; deployment and merging
into dev are separate operations.
