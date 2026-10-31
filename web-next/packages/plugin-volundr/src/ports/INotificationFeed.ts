import type {
  InstanceSeqMap,
  NotificationDelivery,
  NotificationReadState,
  NotificationRule,
  NotificationRuleDraft,
  NotificationServerFilter,
  NotificationSinkOption,
  SessionNotification,
} from '../domain/notifications';

export interface NotificationFeedPage {
  /** Newest first. */
  items: SessionNotification[];
  /** Opaque cursor for the next (older) page; `null` at the end of the feed. */
  nextBefore: string | null;
  /**
   * Opaque "seen through" cursor (max seq per node) to pass back as `after`
   * when gap-filling; `null` when the server does not provide one.
   */
  nextAfter: string | null;
  /** Unread count reported with the page, when the server includes it. */
  unreadCount: number | null;
  /** Nodes the fan-out could not reach for this page. */
  unavailableInstances: string[];
  /**
   * Head seq of every node this page covered (the facade's `instances`),
   * including nodes with no rows; `null` when the server does not report it.
   */
  instanceHeads: InstanceSeqMap | null;
}

/**
 * Where gap-fill resumes: the server's opaque `next_after` when known, else
 * per-instance watermarks the client has seen. The facade only reads the nodes
 * a cursor names (seq 0 reads a node from the start), so watermarks must list
 * every node the client knows about.
 */
export interface NotificationGapCursor {
  cursor: string | null;
  watermarks: InstanceSeqMap;
}

export interface NotificationGapPage {
  /** Oldest first — rows committed after the cursor. */
  items: SessionNotification[];
  /** True when a node's page was full, so more rows may follow. */
  hasMore: boolean;
  /** The cursor to resume from next time, when the server provides one. */
  nextAfter: string | null;
}

export type NotificationStreamStatus = 'open' | 'error';

export interface NotificationFeedSubscriber {
  onNotification(notification: SessionNotification): void;
  /**
   * Stream connection transitions. `open` is reported on every (re)connect —
   * and immediately on subscribe when the shared stream is already open — so
   * subscribers gap-fill with `listSince` there.
   */
  onStatus?(status: NotificationStreamStatus): void;
}

/** Rules, sinks and read-state writes target one Forge node; local when omitted. */
export interface NotificationInstanceOptions {
  instanceId?: string | null;
}

/**
 * The Forge notifications feed across every visible Forge node (contract §4–§6).
 * Cursors are opaque; seqs only compare within one instance.
 */
export interface INotificationFeed {
  list(
    filter: NotificationServerFilter,
    cursor?: { before?: string | null; limit?: number },
  ): Promise<NotificationFeedPage>;
  /** Gap-fill: rows after a cursor, ascending. */
  listSince(
    filter: NotificationServerFilter,
    after: NotificationGapCursor,
    options?: { limit?: number },
  ): Promise<NotificationGapPage>;
  /** One session's notifications after a seq, ascending. */
  listForSession(
    sessionId: string,
    after?: number | null,
    options?: NotificationInstanceOptions & { limit?: number },
  ): Promise<SessionNotification[]>;
  getReadState(): Promise<NotificationReadState>;
  /**
   * Move read watermarks forward for the given instances. Rejects with
   * `NotificationReadStateConflictError` when another reader moved them first.
   */
  markRead(
    through: InstanceSeqMap,
    expected: NotificationReadState,
  ): Promise<NotificationReadState>;
  subscribe(subscriber: NotificationFeedSubscriber): () => void;

  listRules(options?: NotificationInstanceOptions): Promise<NotificationRule[]>;
  createRule(
    draft: NotificationRuleDraft,
    options?: NotificationInstanceOptions,
  ): Promise<NotificationRule>;
  updateRule(
    id: string,
    draft: NotificationRuleDraft,
    options?: NotificationInstanceOptions,
  ): Promise<NotificationRule>;
  deleteRule(id: string, options?: NotificationInstanceOptions): Promise<void>;
  sinks(options?: NotificationInstanceOptions): Promise<NotificationSinkOption[]>;
  deliveries(
    notificationId: string,
    options?: NotificationInstanceOptions,
  ): Promise<NotificationDelivery[]>;
}
