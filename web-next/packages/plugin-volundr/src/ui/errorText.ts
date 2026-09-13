/**
 * The text to show for a failed call: the platform's own answer (the HTTP
 * `detail`, which says what went wrong and where to fix it) when there is
 * one, the error's message otherwise.
 */
export function errorText(error: unknown, fallback = 'Something went wrong'): string {
  if (error && typeof error === 'object') {
    const detail = (error as { detail?: unknown }).detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
