/**
 * Live preview — a port a session listens on, opened on its own origin.
 *
 * The platform serves port `<port>` of session `<id>` at an origin built from a
 * template with `{port}` and `{session_id}` (`GET /api/v1/forge/settings/live-preview`),
 * e.g. `https://{port}--{session_id}.preview.example.com`. The session's Skuld reports
 * which ports are open (`preview_ports` frames, `GET /api/preview/ports`).
 */

export interface LivePreviewConfig {
  enabled: boolean;
  /** Origin of one preview, with `{port}` and `{session_id}` to fill; empty when disabled. */
  originTemplate: string;
}

export const LIVE_PREVIEW_DISABLED: LivePreviewConfig = { enabled: false, originTemplate: '' };

/** What the session's Skuld last reported: the ports open now. */
export interface PreviewPorts {
  enabled: boolean;
  ports: readonly number[];
  /** Set when the last scan failed; the ports are the last good scan's. */
  error: string;
}

export const NO_PREVIEW_PORTS: PreviewPorts = { enabled: false, ports: [], error: '' };

/** A `preview_ports` broker frame: the full set after a change. */
export interface PreviewPortsFrame {
  type: 'preview_ports';
  ports: number[];
  opened: number[];
  closed: number[];
}

export function isPreviewPortsFrame(frame: unknown): frame is PreviewPortsFrame {
  if (typeof frame !== 'object' || frame === null) return false;
  const value = frame as Record<string, unknown>;
  return value.type === 'preview_ports' && Array.isArray(value.ports);
}

function isPort(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 65535;
}

/** The ports a frame reports, sorted, without anything that is not a TCP port. */
export function portsFromFrame(frame: PreviewPortsFrame): number[] {
  return [...new Set(frame.ports.filter(isPort))].sort((a, b) => a - b);
}

/** The origin that serves *port* of *sessionId*, or null when previews are off. */
export function previewOrigin(
  config: LivePreviewConfig,
  sessionId: string,
  port: number,
): string | null {
  if (!config.enabled || !config.originTemplate || !isPort(port)) return null;
  return config.originTemplate
    .replaceAll('{port}', String(port))
    .replaceAll('{session_id}', sessionId);
}

/**
 * The preview URL for *path* (as typed in the URL bar) on *port*. The path only ever
 * moves within the preview origin: the URL is parsed back and anything that resolves
 * to another origin is refused, so the frame and the new-tab link cannot be pointed
 * at a `javascript:` URL or another site.
 */
export function previewUrl(
  config: LivePreviewConfig,
  sessionId: string,
  port: number,
  path = '/',
): string | null {
  const origin = previewOrigin(config, sessionId, port);
  if (origin === null) return null;
  const trimmed = path.trim();
  const base = new URL(origin);
  const url = new URL(trimmed.startsWith('/') ? trimmed : `/${trimmed}`, base);
  if (url.origin !== base.origin) return null;
  return url.href;
}

/**
 * Loopback URLs an agent writes in the transcript (`http://localhost:5173/docs`,
 * `127.0.0.1:3000`, `0.0.0.0:8000`), which the session page turns into preview links.
 */
const LOCAL_URL =
  /\b(?:https?:\/\/)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::(\d{1,5}))(\/[^\s)\]"'`<>]*)?/g;

export interface LocalUrlMatch {
  /** Offset of the match in the text. */
  index: number;
  /** The text matched, e.g. `http://localhost:5173/docs`. */
  text: string;
  port: number;
  path: string;
}

export function findLocalUrls(text: string): LocalUrlMatch[] {
  const matches: LocalUrlMatch[] = [];
  for (const match of text.matchAll(LOCAL_URL)) {
    const port = Number(match[1]);
    if (!isPort(port)) continue;
    // A trailing sentence period is punctuation, not part of the path.
    const raw = match[0].replace(/[.,;:!?]+$/, '');
    const path = (match[2] ?? '/').replace(/[.,;:!?]+$/, '') || '/';
    matches.push({ index: match.index ?? 0, text: raw, port, path });
  }
  return matches;
}
