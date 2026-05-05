/**
 * Format a timestamp (number) or ISO date string to a relative time string
 * such as "3m ago", "2h ago", "1d ago".
 */
export function relTime(input: string | number): string {
  const ms = typeof input === 'number' ? input : new Date(input).getTime();
  if (!ms) return '—';
  const d = Math.max(0, Date.now() - ms);
  if (d < 60_000) return `${Math.floor(d / 1000)}s ago`;
  if (d < 3_600_000) return `${Math.floor(d / 60_000)}m ago`;
  if (d < 86_400_000) return `${Math.floor(d / 3_600_000)}h ago`;
  return `${Math.floor(d / 86_400_000)}d ago`;
}
