/**
 * HTTP adapter for INotificationFeed, talking to the Guild facade under the
 * Forge base path. Live rows ride the shared Forge fleet stream (no second SSE
 * connection); wire shapes live in `./notificationWire`.
 */
import { ApiClientError } from '@niuulabs/query';
import { NotificationReadStateConflictError, type NotificationRule } from '../domain/notifications';
import type { ForgeEventStreamSource } from '../ports/IForgeEventStream';
import type { INotificationFeed } from '../ports/INotificationFeed';
import {
  ALL_INSTANCES,
  READ_STATE_PATH,
  RULES_PATH,
  SESSION_NOTIFICATION_EVENT,
  SINKS_PATH,
  UNAVAILABLE_INSTANCES_HEADER,
  afterCursor,
  deliveriesPath,
  feedQuery,
  instanceHeads,
  isNotificationWire,
  pageCursor,
  normalizeDelivery,
  normalizeNotification,
  normalizeReadState,
  normalizeRule,
  normalizeSink,
  readStatePutBody,
  ruleDraftToWire,
  rulePath,
  sessionNotificationsPath,
  unavailableInstances,
  withInstance,
  type DeliveryWire,
  type NotificationPageWire,
  type NotificationWire,
  type ReadStateWire,
  type RuleWire,
  type SinkWire,
} from './notificationWire';

/** Default page size for the feed (the server caps `limit` at 200). */
export const NOTIFICATION_PAGE_SIZE = 50;
/** Gap-fill reads as much as one request allows. */
export const NOTIFICATION_GAP_PAGE_SIZE = 200;

export interface NotificationHttpClient {
  get<T>(endpoint: string, options?: { signal?: AbortSignal }): Promise<T>;
  /** When available, the feed reads the facade's partial-availability header. */
  getWithHeaders?<T>(
    endpoint: string,
    options?: { signal?: AbortSignal },
  ): Promise<{ data: T; headers: Headers }>;
  post<T>(endpoint: string, body?: unknown): Promise<T>;
  put<T>(endpoint: string, body: unknown): Promise<T>;
  delete<T>(endpoint: string, body?: unknown): Promise<T>;
}

export interface NotificationFeedHttpOptions {
  /** The shared Forge fleet stream; without it the feed has no live updates. */
  stream?: ForgeEventStreamSource | null;
}

function listOf<T>(payload: T[] | Record<string, unknown> | null | undefined, key: string): T[] {
  if (Array.isArray(payload)) return payload;
  const nested = payload?.[key];
  return Array.isArray(nested) ? (nested as T[]) : [];
}

export function buildNotificationFeedHttpAdapter(
  client: NotificationHttpClient,
  options: NotificationFeedHttpOptions = {},
): INotificationFeed {
  // The unavailable-nodes header is readable same-origin (the Guild host); a
  // cross-origin facade must list it in Access-Control-Expose-Headers.
  async function readPage(endpoint: string) {
    if (!client.getWithHeaders)
      return { page: await client.get<NotificationPageWire>(endpoint), unavailable: [] };
    const { data, headers } = await client.getWithHeaders<NotificationPageWire>(endpoint);
    return {
      page: data,
      unavailable: unavailableInstances(headers?.get(UNAVAILABLE_INSTANCES_HEADER)),
    };
  }

  return {
    async list(filter, cursor) {
      const { page, unavailable } = await readPage(
        feedQuery(filter, {
          before: cursor?.before ?? null,
          limit: cursor?.limit ?? NOTIFICATION_PAGE_SIZE,
        }),
      );
      return {
        items: (page.items ?? []).map(normalizeNotification),
        nextBefore: pageCursor(page.next_before),
        nextAfter: pageCursor(page.next_after),
        unreadCount: typeof page.unread_count === 'number' ? page.unread_count : null,
        unavailableInstances: unavailable,
        instanceHeads: instanceHeads(page.instances),
      };
    },

    async listSince(filter, after, gapOptions) {
      const cursor = after.cursor ?? afterCursor(after.watermarks);
      if (!cursor) return { items: [], hasMore: false, nextAfter: null };
      const limit = gapOptions?.limit ?? NOTIFICATION_GAP_PAGE_SIZE;
      const page = await client.get<NotificationPageWire>(
        feedQuery(filter, { after: cursor, limit }),
      );
      const items = (page.items ?? []).map(normalizeNotification);
      return { items, hasMore: items.length >= limit, nextAfter: pageCursor(page.next_after) };
    },

    async listForSession(sessionId, after, sessionOptions) {
      const params = new URLSearchParams();
      if (after !== null && after !== undefined) params.set('after', String(after));
      params.set('limit', String(sessionOptions?.limit ?? NOTIFICATION_PAGE_SIZE));
      const payload = await client.get<NotificationWire[] | NotificationPageWire>(
        withInstance(
          `${sessionNotificationsPath(sessionId)}?${params.toString()}`,
          sessionOptions?.instanceId,
        ),
      );
      return listOf<NotificationWire>(
        payload as NotificationWire[] | Record<string, unknown>,
        'items',
      ).map(normalizeNotification);
    },

    async getReadState() {
      return normalizeReadState(
        await client.get<ReadStateWire>(`${READ_STATE_PATH}?${ALL_INSTANCES}`),
      );
    },

    async markRead(through, expected) {
      if (Object.keys(through).length === 0) return expected;
      const { path, body } = readStatePutBody(through, expected);
      try {
        return normalizeReadState(await client.put<ReadStateWire>(path, body));
      } catch (error) {
        if (error instanceof ApiClientError && error.status === 409)
          throw new NotificationReadStateConflictError();
        throw error;
      }
    },

    subscribe(subscriber) {
      if (!options.stream) return () => {};
      return options.stream.subscribeForgeStream({
        onEvent: (event) => {
          if (event.type !== SESSION_NOTIFICATION_EVENT || !isNotificationWire(event.data)) return;
          subscriber.onNotification(normalizeNotification(event.data));
        },
        onStatus: (status) => subscriber.onStatus?.(status),
      });
    },

    async listRules(ruleOptions) {
      const payload = await client.get<RuleWire[] | Record<string, unknown>>(
        withInstance(RULES_PATH, ruleOptions?.instanceId),
      );
      return listOf<RuleWire>(payload, 'items').map(normalizeRule);
    },

    async createRule(draft, ruleOptions): Promise<NotificationRule> {
      return normalizeRule(
        await client.post<RuleWire>(
          withInstance(RULES_PATH, ruleOptions?.instanceId),
          ruleDraftToWire(draft),
        ),
      );
    },

    async updateRule(id, draft, ruleOptions): Promise<NotificationRule> {
      return normalizeRule(
        await client.put<RuleWire>(
          withInstance(rulePath(id), ruleOptions?.instanceId),
          ruleDraftToWire(draft),
        ),
      );
    },

    async deleteRule(id, ruleOptions) {
      await client.delete(withInstance(rulePath(id), ruleOptions?.instanceId));
    },

    async sinks(sinkOptions) {
      const payload = await client.get<SinkWire[] | Record<string, unknown>>(
        withInstance(SINKS_PATH, sinkOptions?.instanceId),
      );
      return listOf<SinkWire>(payload, 'sinks').map(normalizeSink);
    },

    async deliveries(notificationId, deliveryOptions) {
      const payload = await client.get<DeliveryWire[] | Record<string, unknown>>(
        withInstance(deliveriesPath(notificationId), deliveryOptions?.instanceId),
      );
      return listOf<DeliveryWire>(payload, 'items').map(normalizeDelivery);
    },
  };
}
