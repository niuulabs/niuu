/**
 * Log view filter logic.
 *
 * Pure functions for filtering log entries — unit-testable without React.
 */

import type { Message, MessageKind } from '../domain/message';

export interface LogEntry {
  /** Source message */
  message: Message;
  /** Ravn ID that owns the session this message belongs to */
  ravnId: string;
  /** Ravn persona name */
  personaName: string;
}

export interface LogFilter {
  /** Free-text search against body content */
  query: string;
  /** Only show entries from this ravn (empty = all) */
  ravnId: string;
  /** Only show these kinds (empty = all) */
  kinds: MessageKind[];
}

export const EMPTY_LOG_FILTER: LogFilter = {
  query: '',
  ravnId: '',
  kinds: [],
};

/**
 * Apply a LogFilter to a list of log entries.
 * Returns a new (filtered) array — does not mutate input.
 */
export function applyLogFilter(entries: readonly LogEntry[], filter: LogFilter): LogEntry[] {
  return entries.filter((entry) => {
    if (filter.ravnId && entry.ravnId !== filter.ravnId) return false;
    if (filter.kinds.length > 0 && !filter.kinds.includes(entry.message.kind)) return false;
    if (filter.query) {
      const q = filter.query.toLowerCase();
      const inContent = entry.message.content.toLowerCase().includes(q);
      const inPersona = entry.personaName.toLowerCase().includes(q);
      const inTool = (entry.message.toolName ?? '').toLowerCase().includes(q);
      if (!inContent && !inPersona && !inTool) return false;
    }
    return true;
  });
}
