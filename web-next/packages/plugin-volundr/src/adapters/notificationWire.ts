/**
 * Wire shapes for the Forge notifications API, in one place.
 *
 * Source of truth: docs/forge/session-notifications-contract.md §4 (REST),
 * §5 (SSE) and §6 (Guild facade). The web always calls the facade under the
 * Forge base path with `all_instances=true`; the facade tags each row with
 * `instance_id` and returns opaque cursors. If the backend deviates, adjust
 * this module only.
 */
import {
  isForgeNotificationKind,
  isForgeNotificationSeverity,
  isForgeNotificationSource,
  parseForgeNotificationLinks,
  type ForgeNotificationSeverity,
} from '@niuulabs/domain';
import {
  LOCAL_INSTANCE,
  NOTIFICATION_DELIVERY_STATUSES,
  type InstanceReadState,
  type InstanceSeqMap,
  type NotificationDelivery,
  type NotificationDeliveryStatus,
  type NotificationReadState,
  type NotificationRule,
  type NotificationRuleDraft,
  type NotificationServerFilter,
  type NotificationSinkOption,
  type SessionNotification,
} from '../domain/notifications';

// ── Paths ──────────────────────────────────────────────────────────────────

export const NOTIFICATIONS_PATH = '/notifications';
export const READ_STATE_PATH = '/notifications/read-state';
export const RULES_PATH = '/notifications/rules';
export const SINKS_PATH = '/notifications/sinks';
export const ALL_INSTANCES = 'all_instances=true';
/**
 * Comma-separated ids of nodes the facade fan-out could not reach (contract §6).
 * Browsers only expose it to the page same-origin — the supported setup, with
 * the web served by the Guild host. A cross-origin deployment needs the facade
 * to send `Access-Control-Expose-Headers: X-Forge-Unavailable-Instances`.
 */
export const UNAVAILABLE_INSTANCES_HEADER = 'X-Forge-Unavailable-Instances';
/** SSE event name on `/sessions/stream` (contract §5). */
export const SESSION_NOTIFICATION_EVENT = 'session_notification';

export function rulePath(id: string): string {
  return `${RULES_PATH}/${encodeURIComponent(id)}`;
}

export function deliveriesPath(notificationId: string): string {
  return `${NOTIFICATIONS_PATH}/${encodeURIComponent(notificationId)}/deliveries`;
}

export function sessionNotificationsPath(sessionId: string): string {
  return `/sessions/${encodeURIComponent(sessionId)}/notifications`;
}

/** Route a single-node call to its owner through the facade (`?instance_id=`). */
export function withInstance(path: string, instanceId: string | null | undefined): string {
  if (!instanceId) return path;
  return `${path}${path.includes('?') ? '&' : '?'}instance_id=${encodeURIComponent(instanceId)}`;
}

// ── Payloads ───────────────────────────────────────────────────────────────

export interface NotificationLinkWire {
  label: string;
  url?: string | null;
  kind?: string;
  file_id?: string | null;
}

/** `NotificationResponse` (contract §4); the SSE payload omits `read`. */
export interface NotificationWire {
  id: string;
  seq: number;
  session_id?: string | null;
  session_seq?: number | null;
  session_name?: string | null;
  owner_id?: string;
  tenant_id?: string | null;
  project_id?: string | null;
  kind: string;
  severity: string;
  source: string;
  title: string;
  body?: string;
  links?: NotificationLinkWire[];
  engine?: string | null;
  model?: string | null;
  correlation_id?: string | null;
  created_at: string;
  read?: boolean;
  /** Added by the Guild facade. */
  instance_id?: string | null;
  instance_name?: string | null;
  instance_slug?: string | null;
}

export interface NotificationPageWire {
  items: NotificationWire[];
  /** Opaque at the facade (base64url); a plain seq on a single Forge. */
  next_before?: string | number | null;
  /** Opaque at the facade: the max seq per node, to pass back as `after`. */
  next_after?: string | number | null;
  /** Summed across nodes at the facade. */
  unread_count?: number | null;
  /** Per node at the facade; `head_seq`/`read_through_seq` are then null at the top. */
  instances?: Record<string, InstanceReadStateWire>;
}

export interface InstanceReadStateWire {
  read_through_seq?: number;
  revision?: number;
  unread_count?: number;
  head_seq?: number;
}

/**
 * Facade: `{unread_count, instances: {<id>: {...}}}`. A single Forge answers
 * the plain per-reader shape (optionally tagged with `instance_id`).
 */
export interface ReadStateWire extends InstanceReadStateWire {
  instance_id?: string | null;
  instances?: Record<string, InstanceReadStateWire>;
}

export interface RuleMatchWire {
  kinds?: string[];
  min_severity?: string;
  sources?: string[];
  project_ids?: string[];
  session_ids?: string[];
}

export interface QuietHoursWire {
  start: string;
  end: string;
  timezone: string;
  allow_min_severity?: string;
}

export interface RuleWire {
  id: string;
  name: string;
  enabled?: boolean;
  match?: RuleMatchWire;
  sink: string;
  integration_connection_id?: string | null;
  config?: Record<string, unknown>;
  quiet_hours?: QuietHoursWire | null;
}

export interface SinkWire {
  name: string;
  label?: string;
  requires_integration?: boolean;
}

export interface DeliveryWire {
  id: string;
  notification_id?: string;
  rule_id: string;
  sink: string;
  status: string;
  attempts?: number;
  last_error?: string | null;
  delivered_at?: string | null;
  next_attempt_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

// ── Normalizers ────────────────────────────────────────────────────────────

function text(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null;
}

function severityOr(
  value: unknown,
  fallback: ForgeNotificationSeverity,
): ForgeNotificationSeverity {
  return isForgeNotificationSeverity(value) ? value : fallback;
}

/** Tolerant: an unknown kind/source from a newer Forge still shows as `info`/`system`. */
export function normalizeNotification(wire: NotificationWire): SessionNotification {
  return {
    id: String(wire.id),
    instanceId: text(wire.instance_id),
    instanceName: text(wire.instance_name) ?? text(wire.instance_slug),
    seq: Number(wire.seq) || 0,
    sessionId: text(wire.session_id),
    sessionSeq: typeof wire.session_seq === 'number' ? wire.session_seq : null,
    sessionName: text(wire.session_name),
    ownerId: text(wire.owner_id) ?? '',
    projectId: text(wire.project_id),
    kind: isForgeNotificationKind(wire.kind) ? wire.kind : 'info',
    severity: severityOr(wire.severity, 'info'),
    source: isForgeNotificationSource(wire.source) ? wire.source : 'system',
    title: typeof wire.title === 'string' ? wire.title : '',
    body: typeof wire.body === 'string' ? wire.body : '',
    links: parseForgeNotificationLinks(wire.links),
    engine: text(wire.engine),
    model: text(wire.model),
    correlationId: text(wire.correlation_id),
    createdAt: typeof wire.created_at === 'string' ? wire.created_at : '',
    read: wire.read === true,
  };
}

export function isNotificationWire(value: unknown): value is NotificationWire {
  if (!value || typeof value !== 'object') return false;
  const wire = value as Record<string, unknown>;
  return typeof wire.id === 'string' && typeof wire.seq === 'number';
}

function normalizeInstanceReadState(wire: InstanceReadStateWire): InstanceReadState {
  return {
    readThroughSeq: Number(wire.read_through_seq) || 0,
    revision: Number(wire.revision) || 0,
    unreadCount: Number(wire.unread_count) || 0,
    headSeq: Number(wire.head_seq) || 0,
  };
}

export function normalizeReadState(wire: ReadStateWire): NotificationReadState {
  if (wire.instances && typeof wire.instances === 'object') {
    const instances: Record<string, InstanceReadState> = {};
    for (const [id, state] of Object.entries(wire.instances))
      instances[id] = normalizeInstanceReadState(state);
    const summed = Object.values(instances).reduce((total, state) => total + state.unreadCount, 0);
    return {
      unreadCount: typeof wire.unread_count === 'number' ? wire.unread_count : summed,
      instances,
    };
  }
  const single = normalizeInstanceReadState(wire);
  return {
    unreadCount: single.unreadCount,
    instances: { [text(wire.instance_id) ?? LOCAL_INSTANCE]: single },
  };
}

function toSeverity(value: unknown): ForgeNotificationSeverity {
  return severityOr(value, 'info');
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

export function normalizeRule(wire: RuleWire): NotificationRule {
  const match = wire.match ?? {};
  return {
    id: String(wire.id),
    name: wire.name ?? '',
    enabled: wire.enabled !== false,
    match: {
      kinds: strings(match.kinds).filter(isForgeNotificationKind),
      minSeverity: toSeverity(match.min_severity),
      sources: strings(match.sources).filter(isForgeNotificationSource),
      projectIds: strings(match.project_ids),
      sessionIds: strings(match.session_ids),
    },
    sink: wire.sink ?? '',
    integrationConnectionId: text(wire.integration_connection_id),
    config: wire.config && typeof wire.config === 'object' ? wire.config : {},
    quietHours: wire.quiet_hours
      ? {
          start: wire.quiet_hours.start,
          end: wire.quiet_hours.end,
          timezone: wire.quiet_hours.timezone,
          allowMinSeverity: severityOr(wire.quiet_hours.allow_min_severity, 'critical'),
        }
      : null,
  };
}

export function ruleDraftToWire(draft: NotificationRuleDraft): Omit<RuleWire, 'id'> {
  return {
    name: draft.name.trim(),
    enabled: draft.enabled,
    match: {
      kinds: draft.match.kinds,
      min_severity: draft.match.minSeverity,
      sources: draft.match.sources,
      project_ids: draft.match.projectIds,
      session_ids: draft.match.sessionIds,
    },
    sink: draft.sink,
    integration_connection_id: draft.integrationConnectionId,
    config: draft.config,
    quiet_hours: draft.quietHours
      ? {
          start: draft.quietHours.start,
          end: draft.quietHours.end,
          timezone: draft.quietHours.timezone,
          allow_min_severity: draft.quietHours.allowMinSeverity,
        }
      : null,
  };
}

/** The facade's sink for delivering through the caller's own messaging integration. */
export const INTEGRATION_SINK = 'integration';

export function normalizeSink(wire: SinkWire): NotificationSinkOption {
  return {
    name: wire.name,
    label: wire.label || (wire.name === INTEGRATION_SINK ? 'Messaging integration' : wire.name),
    requiresIntegration: wire.requires_integration === true,
  };
}

function deliveryStatus(value: unknown): NotificationDeliveryStatus {
  return (NOTIFICATION_DELIVERY_STATUSES as readonly string[]).includes(value as string)
    ? (value as NotificationDeliveryStatus)
    : 'pending';
}

export function normalizeDelivery(wire: DeliveryWire): NotificationDelivery {
  return {
    id: String(wire.id),
    ruleId: String(wire.rule_id),
    sink: wire.sink,
    status: deliveryStatus(wire.status),
    attempts: Number(wire.attempts) || 0,
    lastError: text(wire.last_error),
    deliveredAt: text(wire.delivered_at),
    nextAttemptAt: text(wire.next_attempt_at),
    updatedAt: text(wire.updated_at) ?? text(wire.created_at),
  };
}

// ── Queries and cursors ────────────────────────────────────────────────────

/** base64url JSON without padding — the facade's per-instance cursor (§6). */
export function encodeInstanceCursor(map: InstanceSeqMap): string {
  const json = JSON.stringify(map);
  const bytes = new TextEncoder().encode(json);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * The `after` value for gap-fill: a plain seq when only a directly attached
 * Forge is known, otherwise the facade's per-instance cursor.
 */
export function afterCursor(after: InstanceSeqMap): string | null {
  const keys = Object.keys(after);
  if (keys.length === 0) return null;
  if (keys.length === 1 && keys[0] === LOCAL_INSTANCE) return String(after[LOCAL_INSTANCE]);
  const remote: InstanceSeqMap = {};
  for (const key of keys) if (key !== LOCAL_INSTANCE) remote[key] = after[key]!;
  return encodeInstanceCursor(remote);
}

export function feedQuery(
  filter: NotificationServerFilter,
  page: { before?: string | null; after?: string | null; limit: number },
): string {
  const params = new URLSearchParams();
  params.set('all_instances', 'true');
  params.set('limit', String(page.limit));
  if (page.before) params.set('before', page.before);
  if (page.after) params.set('after', page.after);
  if (filter.kinds.length > 0) params.set('kind', filter.kinds.join(','));
  if (filter.minSeverity) params.set('min_severity', filter.minSeverity);
  if (filter.sources.length > 0) params.set('source', filter.sources.join(','));
  if (filter.sessionId) params.set('session_id', filter.sessionId);
  if (filter.projectId) params.set('project_id', filter.projectId);
  if (filter.unreadOnly) params.set('unread', 'true');
  return `${NOTIFICATIONS_PATH}?${params.toString()}`;
}

/** Normalize an opaque (or single-Forge numeric) page cursor. */
export function pageCursor(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined || value === '') return null;
  return String(value);
}

/** Head seq per node from the facade's per-node `instances` summary. */
export function instanceHeads(instances: NotificationPageWire['instances']): InstanceSeqMap | null {
  if (!instances || typeof instances !== 'object') return null;
  const heads: InstanceSeqMap = {};
  for (const [id, state] of Object.entries(instances)) heads[id] = Number(state?.head_seq) || 0;
  return heads;
}

export function unavailableInstances(header: string | null | undefined): string[] {
  return (header ?? '')
    .split(',')
    .map((id) => id.trim())
    .filter(Boolean);
}

/**
 * `PUT /notifications/read-state` body. The facade takes a per-instance map;
 * a directly attached Forge takes the plain single-reader body.
 */
export function readStatePutBody(
  through: InstanceSeqMap,
  expected: NotificationReadState,
): {
  path: string;
  body:
    | { read_through_seq: number; expected_revision: number }
    | { instances: Record<string, { read_through_seq: number; expected_revision: number }> };
} {
  const keys = Object.keys(through);
  if (keys.length === 1 && keys[0] === LOCAL_INSTANCE) {
    return {
      path: READ_STATE_PATH,
      body: {
        read_through_seq: through[LOCAL_INSTANCE]!,
        expected_revision: expected.instances[LOCAL_INSTANCE]?.revision ?? 0,
      },
    };
  }
  const instances: Record<string, { read_through_seq: number; expected_revision: number }> = {};
  for (const key of keys) {
    instances[key] = {
      read_through_seq: through[key]!,
      expected_revision: expected.instances[key]?.revision ?? 0,
    };
  }
  return { path: `${READ_STATE_PATH}?${ALL_INSTANCES}`, body: { instances } };
}
