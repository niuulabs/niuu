import { describe, it, expect, vi, beforeEach, afterEach, afterAll } from 'vitest';
import {
  createApiClient,
  setTokenProvider,
  getAccessToken,
  getAuthHeaders,
  getWebSocketAuth,
  withAuthQuery,
  ApiClientError,
} from './http-client';

async function importFreshHttpClient() {
  vi.resetModules();
  return import('./http-client');
}

function makeFetch(status: number, body: unknown, ok = status >= 200 && status < 300) {
  return vi.fn().mockResolvedValue({
    status,
    ok,
    json: vi.fn().mockResolvedValue(body),
  });
}

describe('setTokenProvider / getAccessToken', () => {
  const originalPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;

  beforeEach(() => {
    window.history.replaceState({}, '', '/');
    window.sessionStorage.clear();
  });

  afterEach(() => setTokenProvider(null));
  afterAll(() => {
    window.history.replaceState({}, '', originalPath);
    window.sessionStorage.clear();
  });

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

  it('builds x-auth headers from local dev identity query params', () => {
    window.history.replaceState(
      {},
      '',
      '/?devUserId=guild-user-a&devTenantId=tenant-a&devEmail=a%40example.com&devRoles=volundr%3Adeveloper%2Cvolundr%3Aviewer',
    );
    const headers = getAuthHeaders();
    expect(headers.get('x-auth-user-id')).toBe('guild-user-a');
    expect(headers.get('x-auth-tenant')).toBe('tenant-a');
    expect(headers.get('x-auth-email')).toBe('a@example.com');
    expect(headers.get('x-auth-roles')).toBe('volundr:developer,volundr:viewer');
  });

  it('adds dev identity query params to websocket urls when no token exists', () => {
    window.history.replaceState(
      {},
      '',
      '/?devUserId=guild-user-b&devTenantId=tenant-b&devRoles=volundr%3Adeveloper',
    );
    expect(withAuthQuery('ws://127.0.0.1:8080/s/abc/session')).toBe(
      'ws://127.0.0.1:8080/s/abc/session?devUserId=guild-user-b&devTenantId=tenant-b&devRoles=volundr%3Adeveloper',
    );
    expect(getWebSocketAuth('ws://127.0.0.1:8080/s/abc/session')).toEqual({
      url: 'ws://127.0.0.1:8080/s/abc/session?devUserId=guild-user-b&devTenantId=tenant-b&devRoles=volundr%3Adeveloper',
    });
  });

  it('remembers local dev identity across navigation without the query string', () => {
    window.history.replaceState(
      {},
      '',
      '/?devUserId=guild-user-a&devTenantId=tenant-a&devEmail=a%40example.com&devRoles=volundr%3Adeveloper',
    );
    expect(getAuthHeaders().get('x-auth-user-id')).toBe('guild-user-a');

    window.history.replaceState({}, '', '/volundr/session/sess-1');

    const headers = getAuthHeaders();
    expect(headers.get('x-auth-user-id')).toBe('guild-user-a');
    expect(headers.get('x-auth-tenant')).toBe('tenant-a');
    expect(withAuthQuery('ws://127.0.0.1:8080/s/abc/session')).toBe(
      'ws://127.0.0.1:8080/s/abc/session?devUserId=guild-user-a&devEmail=a%40example.com&devTenantId=tenant-a&devRoles=volundr%3Adeveloper',
    );
  });

  it('prefers bearer tokens over local dev identity headers', () => {
    setTokenProvider(() => 'live-token');
    window.history.replaceState({}, '', '/?devUserId=guild-user-a&devTenantId=tenant-a');

    const headers = getAuthHeaders();

    expect(headers.get('Authorization')).toBe('Bearer live-token');
    expect(headers.get('x-auth-user-id')).toBeNull();
  });

  it('falls back to the default role when stored dev identity roles are missing', async () => {
    window.sessionStorage.setItem(
      'niuu.devIdentityOverride',
      JSON.stringify({ userId: ' guild-user-c ', email: 42, tenantId: null }),
    );
    const { getAuthHeaders: freshGetAuthHeaders } = await importFreshHttpClient();

    const headers = freshGetAuthHeaders();

    expect(headers.get('x-auth-user-id')).toBe('guild-user-c');
    expect(headers.get('x-auth-roles')).toBe('volundr:developer');
    expect(headers.get('x-auth-email')).toBeNull();
    expect(headers.get('x-auth-tenant')).toBeNull();
  });

  it('filters and trims stored dev identity roles loaded from sessionStorage', async () => {
    window.sessionStorage.setItem(
      'niuu.devIdentityOverride',
      JSON.stringify({
        userId: 'guild-user-d',
        roles: [' review ', '', 7, 'ship'],
      }),
    );
    const { getAuthHeaders: freshGetAuthHeaders } = await importFreshHttpClient();

    expect(freshGetAuthHeaders().get('x-auth-roles')).toBe('review,ship');
  });

  it('ignores malformed or incomplete stored dev identity payloads', async () => {
    window.sessionStorage.setItem('niuu.devIdentityOverride', '{');
    let fresh = await importFreshHttpClient();
    expect(fresh.getAuthHeaders().get('x-auth-user-id')).toBeNull();

    window.sessionStorage.setItem('niuu.devIdentityOverride', JSON.stringify({ userId: '   ' }));
    fresh = await importFreshHttpClient();
    expect(fresh.getAuthHeaders().get('x-auth-user-id')).toBeNull();
  });

  it('handles missing browser storage and write failures gracefully', async () => {
    const originalSessionStorage = window.sessionStorage;
    const setItemSpy = vi.spyOn(window.sessionStorage, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });

    window.history.replaceState({}, '', '/?devUserId=guild-user-e');
    let fresh = await importFreshHttpClient();
    expect(fresh.getAuthHeaders().get('x-auth-user-id')).toBe('guild-user-e');

    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      value: undefined,
    });
    window.history.replaceState({}, '', '/');
    fresh = await importFreshHttpClient();
    expect(fresh.getAuthHeaders().get('x-auth-user-id')).toBeNull();

    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      value: originalSessionStorage,
    });
    setItemSpy.mockRestore();
  });

  it('supports auth query resolution without a browser window', async () => {
    const originalWindow = globalThis.window;
    vi.stubGlobal('window', undefined);
    const fresh = await importFreshHttpClient();

    expect(fresh.getAuthHeaders().get('Authorization')).toBeNull();
    expect(fresh.withAuthQuery('/stream')).toBe('http://localhost/stream');

    vi.unstubAllGlobals();
    vi.stubGlobal('window', originalWindow);
  });

  it('adds access tokens to websocket urls for Envoy-compatible browser auth', async () => {
    const fresh = await importFreshHttpClient();
    fresh.setTokenProvider(() => 'token-xyz');

    expect(fresh.withAuthQuery('ws://127.0.0.1:8080/s/abc/session')).toBe(
      'ws://127.0.0.1:8080/s/abc/session?access_token=token-xyz',
    );
    expect(fresh.getWebSocketAuth('ws://127.0.0.1:8080/s/abc/session')).toEqual({
      url: 'ws://127.0.0.1:8080/s/abc/session?access_token=token-xyz',
    });
  });

  it('allows local dev identity overrides on private LAN hosts', async () => {
    const originalWindow = globalThis.window;
    const fakeSessionStorage = {
      getItem: vi.fn().mockReturnValue(null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
      clear: vi.fn(),
      key: vi.fn(),
      length: 0,
    };
    vi.stubGlobal('window', {
      location: new URL(
        'http://192.168.1.106/volundr?devUserId=guild-user-lan&devTenantId=tenant-lan&devRoles=volundr%3Adeveloper',
      ),
      sessionStorage: fakeSessionStorage,
    });

    const fresh = await importFreshHttpClient();

    expect(fresh.getAuthHeaders().get('x-auth-user-id')).toBe('guild-user-lan');
    expect(fresh.withAuthQuery('/stream')).toBe(
      'http://192.168.1.106/stream?devUserId=guild-user-lan&devTenantId=tenant-lan&devRoles=volundr%3Adeveloper',
    );

    vi.unstubAllGlobals();
    vi.stubGlobal('window', originalWindow);
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

  it('POST without a payload omits the request body', async () => {
    const client = createApiClient(BASE);
    await client.post('/items');
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(opts.body).toBeUndefined();
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
    expect((opts.headers as Headers).get('Authorization')).toBe('Bearer bearer-xyz');
  });

  it('omits Authorization header when token is null', async () => {
    setTokenProvider(() => null);
    const client = createApiClient(BASE);
    await client.get('/items');
    const [, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((opts.headers as Headers).get('Authorization')).toBeNull();
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
