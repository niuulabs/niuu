/**
 * What an API failure says, in the server's own words.
 *
 * The HTTP client's message is only the status ("API request failed: 503");
 * the reason is in `detail`, which a proxied instance may wrap in one more
 * `{"detail": …}` layer.
 */
function unwrapDetail(detail: string): string {
  const trimmed = detail.trim();
  if (!trimmed.startsWith('{')) return trimmed;
  try {
    const parsed = JSON.parse(trimmed) as { detail?: unknown };
    return typeof parsed.detail === 'string' ? unwrapDetail(parsed.detail) : trimmed;
  } catch {
    return trimmed;
  }
}

export function errorText(error: unknown, fallback: string): string {
  if (!error) return fallback;
  const detail = (error as { detail?: unknown }).detail;
  if (typeof detail === 'string' && detail.trim()) return unwrapDetail(detail);
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
