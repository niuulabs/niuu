/**
 * LogView — monospace event stream across all sessions.
 *
 * Columns: time · raven · kind · body
 * Features: free-text filter, kind filter, auto-tail toggle.
 */

import { useState, useEffect, useRef, useMemo } from 'react';
import { useSessions, useMessages } from './hooks/useSessions';
import { useRavens } from './hooks/useRavens';
import {
  applyLogFilter,
  EMPTY_LOG_FILTER,
  type LogEntry,
  type LogFilter,
} from '../application/logFilter';
import type { MessageKind } from '../domain/message';

const ALL_KINDS: MessageKind[] = [
  'user',
  'asst',
  'system',
  'tool_call',
  'tool_result',
  'emit',
  'think',
];

const KIND_COLOR: Record<MessageKind, string> = {
  user: 'rv-log-row__kind--user',
  asst: 'rv-log-row__kind--asst',
  system: 'rv-log-row__kind--system',
  tool_call: 'rv-log-row__kind--tool-call',
  tool_result: 'rv-log-row__kind--tool-result',
  emit: 'rv-log-row__kind--emit',
  think: 'rv-log-row__kind--think',
};

import { formatTime } from './formatTime';

function truncateBody(content: string, maxLen = 120): string {
  if (content.length <= maxLen) return content;
  return content.slice(0, maxLen) + '…';
}

// ---------------------------------------------------------------------------
// Hooks to assemble log entries
// ---------------------------------------------------------------------------

/** Fetches messages for a single session and returns (ravnId, personaName, messages). */
function useSessionLog(sessionId: string, ravnId: string, personaName: string) {
  const { data: messages } = useMessages(sessionId);
  return useMemo<LogEntry[]>(
    () =>
      (messages ?? []).map((message) => ({
        message,
        ravnId,
        personaName,
      })),
    [messages, ravnId, personaName],
  );
}

/** Assembles a sorted log from all sessions. */
function useAllLogEntries(): LogEntry[] {
  const { data: sessions } = useSessions();
  const { data: ravens } = useRavens();

  // Build ravnId -> personaName map
  const ravenMap = useMemo(() => {
    const m = new Map<string, string>();
    (ravens ?? []).forEach((r) => m.set(r.id, r.personaName));
    return m;
  }, [ravens]);

  // Flatten sessions to log entries (we call useSessionLog for each)
  // Note: hook count must be stable — we pass up to 10 sessions (mock has 6)
  const MAX_SESSIONS = 10;
  const padded = useMemo(() => {
    const arr = sessions ?? [];
    return Array.from({ length: MAX_SESSIONS }, (_, i) => arr[i] ?? null);
  }, [sessions]);

  const e0 = useSessionLog(
    padded[0]?.id ?? '',
    padded[0]?.ravnId ?? '',
    padded[0]?.personaName ?? '',
  );
  const e1 = useSessionLog(
    padded[1]?.id ?? '',
    padded[1]?.ravnId ?? '',
    padded[1]?.personaName ?? '',
  );
  const e2 = useSessionLog(
    padded[2]?.id ?? '',
    padded[2]?.ravnId ?? '',
    padded[2]?.personaName ?? '',
  );
  const e3 = useSessionLog(
    padded[3]?.id ?? '',
    padded[3]?.ravnId ?? '',
    padded[3]?.personaName ?? '',
  );
  const e4 = useSessionLog(
    padded[4]?.id ?? '',
    padded[4]?.ravnId ?? '',
    padded[4]?.personaName ?? '',
  );
  const e5 = useSessionLog(
    padded[5]?.id ?? '',
    padded[5]?.ravnId ?? '',
    padded[5]?.personaName ?? '',
  );
  const e6 = useSessionLog(
    padded[6]?.id ?? '',
    padded[6]?.ravnId ?? '',
    padded[6]?.personaName ?? '',
  );
  const e7 = useSessionLog(
    padded[7]?.id ?? '',
    padded[7]?.ravnId ?? '',
    padded[7]?.personaName ?? '',
  );
  const e8 = useSessionLog(
    padded[8]?.id ?? '',
    padded[8]?.ravnId ?? '',
    padded[8]?.personaName ?? '',
  );
  const e9 = useSessionLog(
    padded[9]?.id ?? '',
    padded[9]?.ravnId ?? '',
    padded[9]?.personaName ?? '',
  );

  return useMemo(() => {
    const all = [...e0, ...e1, ...e2, ...e3, ...e4, ...e5, ...e6, ...e7, ...e8, ...e9];
    // Enrich ravnId from ravenMap where possible
    const enriched = all.map((entry) => ({
      ...entry,
      personaName: ravenMap.get(entry.ravnId) ?? entry.personaName,
    }));
    return enriched.sort((a, b) => a.message.ts.localeCompare(b.message.ts));
  }, [e0, e1, e2, e3, e4, e5, e6, e7, e8, e9, ravenMap]);
}

// ---------------------------------------------------------------------------
// Log row
// ---------------------------------------------------------------------------

function LogRow({ entry }: { entry: LogEntry }) {
  const { message, personaName } = entry;
  const body = message.toolName ? `[${message.toolName}] ${message.content}` : message.content;

  return (
    <tr className="rv-log-row" data-kind={message.kind}>
      <td className="rv-log-row__time">{formatTime(message.ts)}</td>
      <td className="rv-log-row__raven">{personaName}</td>
      <td className={`rv-log-row__kind ${KIND_COLOR[message.kind]}`}>{message.kind}</td>
      <td className="rv-log-row__body">{truncateBody(body)}</td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function LogView() {
  const allEntries = useAllLogEntries();
  const { data: ravens } = useRavens();

  const [filter, setFilter] = useState<LogFilter>(EMPTY_LOG_FILTER);
  const [autoTail, setAutoTail] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => applyLogFilter(allEntries, filter), [allEntries, filter]);

  useEffect(() => {
    if (autoTail) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [filtered, autoTail]);

  const toggleKind = (kind: MessageKind) => {
    setFilter((prev) => {
      const kinds = prev.kinds.includes(kind)
        ? prev.kinds.filter((k) => k !== kind)
        : [...prev.kinds, kind];
      return { ...prev, kinds };
    });
  };

  return (
    <div className="rv-log-view">
      {/* ── Filter bar ──────────────────────────────────────────────── */}
      <div className="rv-log-view__filters" role="search" aria-label="log filters">
        <input
          type="search"
          className="rv-log-view__search"
          placeholder="filter log…"
          value={filter.query}
          aria-label="search log"
          onChange={(e) => setFilter((prev) => ({ ...prev, query: e.target.value }))}
        />
        <select
          className="rv-log-view__raven-select"
          value={filter.ravnId}
          aria-label="filter by raven"
          onChange={(e) => setFilter((prev) => ({ ...prev, ravnId: e.target.value }))}
        >
          <option value="">all ravens</option>
          {(ravens ?? []).map((r) => (
            <option key={r.id} value={r.id}>
              {r.personaName}
            </option>
          ))}
        </select>
        <div className="rv-log-view__kind-filters" role="group" aria-label="kind filters">
          {ALL_KINDS.map((kind) => (
            <button
              key={kind}
              type="button"
              className={`rv-log-view__kind-btn${filter.kinds.includes(kind) ? ' rv-log-view__kind-btn--active' : ''}`}
              aria-pressed={filter.kinds.includes(kind)}
              onClick={() => toggleKind(kind)}
            >
              {kind}
            </button>
          ))}
        </div>
        <label className="rv-log-view__auto-tail">
          <input
            type="checkbox"
            checked={autoTail}
            onChange={(e) => setAutoTail(e.target.checked)}
            aria-label="auto-tail"
          />
          auto-tail
        </label>
      </div>

      {/* ── Stream table ─────────────────────────────────────────────── */}
      <div className="rv-log-view__stream" role="log" aria-label="event log">
        <table className="rv-log-table" aria-label="log stream">
          <thead>
            <tr>
              <th className="rv-log-table__th--time">time</th>
              <th className="rv-log-table__th--raven">raven</th>
              <th className="rv-log-table__th--kind">kind</th>
              <th className="rv-log-table__th--body">body</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((entry) => (
              <LogRow key={entry.message.id} entry={entry} />
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <div className="rv-log-view__empty">
            {allEntries.length === 0 ? 'no log entries yet' : 'no entries match the current filter'}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="rv-log-view__footer">
        {filtered.length} / {allEntries.length} entries
      </div>
    </div>
  );
}
