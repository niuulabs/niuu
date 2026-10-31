/**
 * Forge session notifications — the value types shared by the inline chat card
 * (`@niuulabs/ui`) and the Forge notifications feed (`@niuulabs/plugin-volundr`).
 *
 * Mirrors the backend contract in `src/niuu/domain/notifications.py` and
 * `docs/forge/session-notifications-contract.md` §1. The parser is tolerant:
 * transcripts outlive releases, so an unknown kind or severity degrades to a
 * neutral value instead of hiding the notification.
 */

export const FORGE_NOTIFICATION_KINDS = [
  'milestone',
  'decision',
  'attention',
  'reply_ready',
  'error',
  'info',
] as const;
export type ForgeNotificationKind = (typeof FORGE_NOTIFICATION_KINDS)[number];

/** Ordered from least to most urgent; `severityRank` relies on this order. */
export const FORGE_NOTIFICATION_SEVERITIES = ['info', 'success', 'warning', 'critical'] as const;
export type ForgeNotificationSeverity = (typeof FORGE_NOTIFICATION_SEVERITIES)[number];

export const FORGE_NOTIFICATION_SOURCES = ['agent', 'system', 'operator'] as const;
export type ForgeNotificationSource = (typeof FORGE_NOTIFICATION_SOURCES)[number];

export const FORGE_NOTIFICATION_LINK_KINDS = ['url', 'file', 'pr', 'session', 'artifact'] as const;
export type ForgeNotificationLinkKind = (typeof FORGE_NOTIFICATION_LINK_KINDS)[number];

/** Tool part name of the notification turn the broker appends to the session log. */
export const FORGE_NOTIFICATION_TOOL_NAME = 'forge_notification';

export interface ForgeNotificationLink {
  label: string;
  url: string | null;
  kind: ForgeNotificationLinkKind;
  fileId: string | null;
}

/** The `tool_use` input of a `forge_notification` part (contract §1). */
export interface ForgeNotificationPayload {
  notificationId: string | null;
  kind: ForgeNotificationKind;
  severity: ForgeNotificationSeverity;
  title: string;
  body: string;
  links: ForgeNotificationLink[];
  correlationId: string | null;
}

function oneOf<T extends string>(values: readonly T[], value: unknown, fallback: T): T {
  return includes(values, value) ? (value as T) : fallback;
}

function optionalString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

function includes(values: readonly string[], value: unknown): boolean {
  return typeof value === 'string' && values.includes(value);
}

export function isForgeNotificationKind(value: unknown): value is ForgeNotificationKind {
  return includes(FORGE_NOTIFICATION_KINDS, value);
}

export function isForgeNotificationSeverity(value: unknown): value is ForgeNotificationSeverity {
  return includes(FORGE_NOTIFICATION_SEVERITIES, value);
}

export function isForgeNotificationSource(value: unknown): value is ForgeNotificationSource {
  return includes(FORGE_NOTIFICATION_SOURCES, value);
}

/** Comparable rank; unknown values rank lowest, like the backend's `severity_rank`. */
export function severityRank(severity: string | null | undefined): number {
  const index = (FORGE_NOTIFICATION_SEVERITIES as readonly string[]).indexOf(severity ?? '');
  return index < 0 ? 0 : index;
}

export function parseForgeNotificationLinks(value: unknown): ForgeNotificationLink[] {
  if (!Array.isArray(value)) return [];
  const links: ForgeNotificationLink[] = [];
  for (const raw of value) {
    if (!raw || typeof raw !== 'object') continue;
    const link = raw as Record<string, unknown>;
    const label = optionalString(link.label);
    const url = optionalString(link.url);
    const fileId = optionalString(link.file_id ?? link.fileId);
    if (!label || (!url && !fileId)) continue;
    links.push({
      label,
      url,
      kind: oneOf(FORGE_NOTIFICATION_LINK_KINDS, link.kind, fileId ? 'file' : 'url'),
      fileId,
    });
  }
  return links;
}

/**
 * Read a notification from a turn part input (or a metadata copy of it).
 * Returns `null` only when there is nothing to show (no title).
 */
export function parseForgeNotificationPayload(input: unknown): ForgeNotificationPayload | null {
  if (!input || typeof input !== 'object') return null;
  const raw = input as Record<string, unknown>;
  const title = typeof raw.title === 'string' ? raw.title.trim() : '';
  if (!title) return null;
  return {
    notificationId: optionalString(raw.notification_id ?? raw.notificationId),
    kind: oneOf(FORGE_NOTIFICATION_KINDS, raw.kind, 'info'),
    severity: oneOf(FORGE_NOTIFICATION_SEVERITIES, raw.severity, 'info'),
    title,
    body: typeof raw.body === 'string' ? raw.body : '',
    links: parseForgeNotificationLinks(raw.links),
    correlationId: optionalString(raw.correlation_id ?? raw.correlationId),
  };
}

const NOTIFY_CALL_NAMES = new Set([
  'mcp__forge__notify',
  'mcp__forge__forge_notify',
  'forge.notify',
  'forge.forge_notify',
  'forge__notify',
  'forge:notify',
  'forge/notify',
  'forge_notify',
]);

/**
 * Whether a raw tool call is the model's Forge MCP `notify` call that produced a
 * notification card. Claude names MCP tools `mcp__<server>__<tool>`; Codex MCP
 * calls are transcribed as `forge.notify` or, by older brokers, as the bare tool
 * name — the bare `notify` only counts when its input is a notification draft.
 */
export function isForgeNotifyCall(name: string, input?: Record<string, unknown> | null): boolean {
  const normalized = name.trim().toLowerCase();
  if (NOTIFY_CALL_NAMES.has(normalized)) return true;
  if (normalized !== 'notify' || !input) return false;
  return isForgeNotificationKind(input.kind) && typeof input.title === 'string';
}
