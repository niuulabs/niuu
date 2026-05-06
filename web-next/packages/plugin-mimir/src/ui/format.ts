export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

export function formatTimestamp(
  iso: string,
  dateStyle: 'short' | 'medium' | 'long' = 'short',
): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle,
    timeStyle: 'short',
  });
}
