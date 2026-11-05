/** Compact, rounded counts keep the conversation header quiet. */
export function formatTokenCount(count: number): string {
  if (!Number.isFinite(count) || count < 0) return '0';
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 0,
  })
    .format(count)
    .toLowerCase();
}
