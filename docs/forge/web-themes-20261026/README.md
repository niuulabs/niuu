# Web colour themes — rendered proof (LEX-1495, 2026-10-26)

Sessions page of the production web bundle (`vite preview`) with the
`forge-ux-parity` e2e fixture (mocked Forge API; the amber "optional service
backend is unavailable" banner is part of that fixture, not of the theme).

| Theme | Desktop 1440×900 | Phone 390×844 (list) | Phone 390×844 (session) |
|---|---|---|---|
| Midnight Blue (default) | `sessions-midnight-blue-desktop.png` | `sessions-midnight-blue-phone.png` | `session-chat-midnight-blue-phone.png` |
| Deep Blue | `sessions-deep-blue-desktop.png` | `sessions-deep-blue-phone.png` | `session-chat-deep-blue-phone.png` |
| Light (upstream) | `sessions-light-desktop.png` | `sessions-light-phone.png` | `session-chat-light-phone.png` |

The picker (moon/sun button, top right) also offers Dark Blue, blue (xteo),
Native dark, Amber and Spring. The choice persists as `niuu.theme` in
localStorage.
