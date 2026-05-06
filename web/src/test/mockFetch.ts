/**
 * Shared fetch mock helper for adapter tests.
 *
 * Usage:
 *   import { mockResponse } from '@/test/mockFetch';
 *   mockFetch.mockReturnValueOnce(mockResponse({ data: 'ok' }));
 *   mockFetch.mockReturnValueOnce(mockResponse(null, 500));
 */
export function mockResponse(data: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
  } as Response);
}
