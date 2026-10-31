/**
 * The shared Forge fleet event stream (`/sessions/stream?all_instances=true`).
 *
 * One SSE connection serves the whole Forge plugin; features that need raw
 * events (such as the notifications feed) listen on it instead of opening a
 * second stream.
 */
export interface ForgeStreamEvent {
  /** SSE event name, e.g. `session_notification`. */
  type: string;
  /** Parsed JSON payload. */
  data: unknown;
}

export interface ForgeStreamListener {
  onEvent(event: ForgeStreamEvent): void;
  /** `open` on every (re)connect and on subscribe while open; `error` when it drops. */
  onStatus?(status: 'open' | 'error'): void;
}

export interface ForgeEventStreamSource {
  subscribeForgeStream(listener: ForgeStreamListener): () => void;
}
