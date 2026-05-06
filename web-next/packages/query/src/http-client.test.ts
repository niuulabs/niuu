import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createApiClient, setTokenProvider, getAccessToken, ApiClientError } from './http-client';

function makeFetch(status: number, body: unknown, ok = status >= 200 && status < 300) {
  return vi.fn().mockResolvedValue({
    status,
    ok,
    json: vi.fn().mockResolvedValue(body),
  });
}

describe('setTokenProvider / getAccessToken', () => {
  afterEach(() => setTokenProvider(null));

  it('returns null when no provider is set', () => {
    setTokenProvider(null);
    expect(getAccessToken()).toBeNull();
  });

  it('returns the token from the registered provider', () => {
    setTokenProvider(() => 'test-token');
    expect(getAccessToken()).toBe('test-token');
  });

  it('accepts null to clear the provider', () => {
    setTokenProvider(() => 'tok');
    setTokenProvider(null);
    expect(getAccessToken()).toBeNull();
  });
});

describe('createApiClient', () => {
  const BASE = '/api/v1/test';
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    setTokenProvider(null);
    fetchMock = makeFetch(200, { ok: true });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setTokenProvider(null);
  });

  it('GET prepends basePath and sends GET method', async () => {
    const client = createApiClient(BASE);
    expect((client as { basePath?: string }).basePath).toBe(BASE);
    await client.get('/items');
    expect(fetchMock).toHaveBeenCalledWith(
      `${BASE}/items`,
      expect.objectContaining({ method: 'GET' }),
    );
  });

  it('POST sends body as JSON', async () => {
    const client = createApiClient(BASE);
    await client.post('/items', { name: 'x' });
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(opts.method).toBe('POST');
    expect(opts.body).toBe(JSON.stringify({ name: 'x' }));
  });

  it('POST with FormData passes it through without JSON.stringify', async () => {
    const client = createApiClient(BASE);
    const form = new FormData();
    form.append('file', new Blob(['hello'], { type: 'text/plain' }), 'hello.txt');
    await client.post('/upload', form);
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(opts.method).toBe('POST');
    expect(opts.body).toBe(form);
  });

  it('POST with FormData omits Content-Type header so browser sets multipart boundary', async () => {
    const client = createApiClient(BASE);
    const form = new FormData();
    await client.post('/upload', form);
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((opts.headers as Record<string, string>)['Content-Type']).toBeUndefined();
  });

  it('PUT sends body as JSON', async () => {
    const client = createApiClient(BASE);
    await client.put('/items/1', { name: 'y' });
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(opts.method).toBe('PUT');
    expect(opts.body).toBe(JSON.stringify({ name: 'y' }));
  });

  it('PATCH sends body as JSON', async () => {
    const client = createApiClient(BASE);
    await client.patch('/items/1', { active: false });
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(opts.method).toBe('PATCH');
  });

  it('DELETE with no body omits body', async () => {
    const client = createApiClient(BASE);
    await client.delete('/items/1');
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(opts.method).toBe('DELETE');
    expect(opts.body).toBeUndefined();
  });

  it('DELETE with body includes body', async () => {
    const client = createApiClient(BASE);
    await client.delete('/items/1', { reason: 'test' });
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(opts.body).toBe(JSON.stringify({ reason: 'test' }));
  });

  it('injects Authorization header when token provider is set', async () => {
    setTokenProvider(() => 'bearer-xyz');
    const client = createApiClient(BASE);
    await client.get('/items');
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((opts.headers as Record<string, string>)['Authorization']).toBe('Bearer bearer-xyz');
  });

  it('omits Authorization header when token is null', async () => {
    setTokenProvider(() => null);
    const client = createApiClient(BASE);
    await client.get('/items');
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((opts.headers as Record<string, string>)['Authorization']).toBeUndefined();
  });

  it('returns undefined for 204 No Content without parsing body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 204, ok: true, json: vi.fn() }));
    const client = createApiClient(BASE);
    const result = await client.delete('/items/1');
    expect(result).toBeUndefined();
  });

  it('throws ApiClientError on non-ok response', async () => {
    vi.stubGlobal('fetch', makeFetch(404, { detail: 'not found' }, false));
    const client = createApiClient(BASE);
    await expect(client.get('/missing')).rejects.toThrow(ApiClientError);
  });

  it('ApiClientError carries status and detail', async () => {
    vi.stubGlobal('fetch', makeFetch(422, { detail: 'invalid input' }, false));
    const client = createApiClient(BASE);
    try {
      await client.get('/bad');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiClientError);
      expect((err as ApiClientError).status).toBe(422);
      expect((err as ApiClientError).detail).toBe('invalid input');
    }
  });

  it('uses "Unknown error" when detail field is missing', async () => {
    vi.stubGlobal('fetch', makeFetch(500, {}, false));
    const client = createApiClient(BASE);
    try {
      await client.get('/boom');
    } catch (err) {
      expect((err as ApiClientError).detail).toBe('Unknown error');
    }
  });
});
