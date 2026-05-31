export function normalizeThreshold(value: number): number {
  return value > 1 ? value / 100 : value;
}

export function formatThreshold(value: number): string {
  return normalizeThreshold(value).toFixed(2);
}
