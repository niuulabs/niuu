/**
 * Filtering a resident's runtime log buffer by severity and text.
 */

import type { ResidentLogEntry } from '../ports';

export type LogSeverity = 'all' | 'warnings' | 'errors';

export const LOG_SEVERITIES: Array<{ id: LogSeverity; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'warnings', label: 'Warnings' },
  { id: 'errors', label: 'Errors' },
];

const ERROR_LEVELS = new Set(['error', 'critical', 'fatal']);
const WARNING_LEVELS = new Set(['warn', 'warning', ...ERROR_LEVELS]);

export function normalizeLevel(level: string): string {
  return level.trim().toLowerCase() || 'info';
}

function matchesSeverity(entry: ResidentLogEntry, severity: LogSeverity): boolean {
  const level = normalizeLevel(entry.level);
  switch (severity) {
    case 'all':
      return true;
    case 'warnings':
      return WARNING_LEVELS.has(level);
    case 'errors':
      return ERROR_LEVELS.has(level);
  }
}

export function formatLogFields(fields: Record<string, string>): string {
  return Object.entries(fields)
    .map(([key, value]) => `${key}=${value}`)
    .join(' ');
}

export function filterResidentLogs(
  entries: readonly ResidentLogEntry[],
  severity: LogSeverity,
  query: string,
): ResidentLogEntry[] {
  const needle = query.trim().toLowerCase();
  return entries.filter((entry) => {
    if (!matchesSeverity(entry, severity)) return false;
    if (!needle) return true;
    return [entry.message, entry.source, entry.target, formatLogFields(entry.fields)]
      .join(' ')
      .toLowerCase()
      .includes(needle);
  });
}
