import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  forwardSessionHost,
  localizeRemoteConfig,
  remoteDev,
  sessionDomain,
  sessionHostFetchShim,
  sessionHostTarget,
} from './remoteDev';

const ORIGIN = 'https://yggdrasil.niuu.world';

describe('localizeRemoteConfig', () => {
  it('makes every service local and proxies the ones on another host', () => {
    const { config, proxy } = localizeRemoteConfig(
      {
        theme: 'niuu',
        services: {
          forge: { mode: 'http', baseUrl: `${ORIGIN}/api/v1/forge` },
          mimir: { mode: 'http', baseUrl: 'https://mimir.niuu.world/api/v1/mimir' },
          identity: { mode: 'http', baseUrl: '/api/v1' },
          pty: { mode: 'ws', wsUrl: '/s/{sessionId}/session' },
        },
      },
      ORIGIN,
    );
    expect(config.theme).toBe('niuu');
    expect(config.services.forge?.baseUrl).toBe('/api/v1/forge');
    expect(config.services.mimir?.baseUrl).toBe('/api/v1/mimir');
    expect(config.services.identity?.baseUrl).toBe('/api/v1');
    expect(config.services.pty).toEqual({ mode: 'ws', wsUrl: '/s/{sessionId}/session' });
    expect(proxy).toEqual({
      '/api/v1/mimir': { target: 'https://mimir.niuu.world', changeOrigin: true },
    });
  });
});

describe('remoteDev', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('stops when the target is not a deployed Niuu', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 404 })));
    await expect(remoteDev(ORIGIN)).rejects.toThrow('answered 404');
  });

  it('serves the cluster config in place of the local files', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          Response.json({ services: { forge: { baseUrl: `${ORIGIN}/api/v1/forge` } } }),
        ),
    );
    const { origin, plugin } = await remoteDev(`${ORIGIN}/`);
    expect(origin).toBe(ORIGIN);

    const handlers: Array<(req: unknown, res: unknown, next: () => void) => void> = [];
    const server = { middlewares: { use: (fn: (typeof handlers)[number]) => handlers.push(fn) } };
    (plugin.configureServer as (value: unknown) => void)(server);
    const handler = handlers[0]!;

    const response = { setHeader: vi.fn(), end: vi.fn() };
    const next = vi.fn();
    handler({ url: '/config.json?v=1' }, response, next);
    expect(next).not.toHaveBeenCalled();
    expect(JSON.parse(response.end.mock.calls[0]![0] as string).services.forge.baseUrl).toBe(
      '/api/v1/forge',
    );

    handler({ url: '/index.html' }, response, next);
    expect(next).toHaveBeenCalledOnce();
  });
});

describe('session hosts', () => {
  const DOMAIN = 'niuu.world';
  const HOST = 'sessions.valhalla.asgard.niuu.world';
  let upstream: Server | undefined;
  let local: Server | undefined;

  afterEach(() => {
    upstream?.close();
    local?.close();
    upstream = local = undefined;
    vi.unstubAllGlobals();
  });

  it('admits the hosts under the remote parent domain', () => {
    expect(sessionDomain(ORIGIN)).toBe(DOMAIN);
    expect(() => sessionDomain('https://niuu.world')).toThrow('no parent domain');
  });

  it('forwards only session paths on a host of this cluster', () => {
    expect(
      sessionHostTarget(`/__session-host/${HOST}/s/abc/api/diff/files?base=x`, DOMAIN)?.href,
    ).toBe(`https://${HOST}/s/abc/api/diff/files?base=x`);
    expect(sessionHostTarget('/__session-host/evil.example.com/s/abc', DOMAIN)).toBeNull();
    expect(sessionHostTarget(`/__session-host/${HOST}/api/v1/forge`, DOMAIN)).toBeNull();
    expect(sessionHostTarget(`/__session-host/${HOST}`, DOMAIN)).toBeNull();
    expect(sessionHostTarget('/api/v1/forge', DOMAIN)).toBeNull();
  });

  it('sends a page fetch of a session host through the dev server', async () => {
    const direct = vi.fn().mockResolvedValue(new Response('ok'));
    vi.stubGlobal('window', { fetch: direct });
    vi.stubGlobal('location', {
      href: 'http://localhost:5199/forge',
      origin: 'http://localhost:5199',
    });
    new Function(sessionHostFetchShim(DOMAIN))();
    const shimmed = (globalThis as unknown as { window: { fetch: typeof fetch } }).window.fetch;

    await shimmed(`https://${HOST}/s/abc/api/diff/files?base=last-commit`, { method: 'GET' });
    expect(direct).toHaveBeenLastCalledWith(
      `http://localhost:5199/__session-host/${HOST}/s/abc/api/diff/files?base=last-commit`,
      { method: 'GET' },
    );

    await shimmed(new Request(`https://${HOST}/s/abc/api/files`, { method: 'POST', body: 'x' }));
    const [forwardedUrl, forwarded] = direct.mock.lastCall as [string, RequestInit];
    expect(forwardedUrl).toBe(`http://localhost:5199/__session-host/${HOST}/s/abc/api/files`);
    expect(forwarded.method).toBe('POST');
    expect(new TextDecoder().decode(forwarded.body as ArrayBuffer)).toBe('x');

    await shimmed('/api/v1/forge/sessions');
    expect(direct).toHaveBeenLastCalledWith('/api/v1/forge/sessions', undefined);
    await shimmed('https://evil.example.com/s/abc');
    expect(direct).toHaveBeenLastCalledWith('https://evil.example.com/s/abc', undefined);
  });

  async function listen(server: Server) {
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    return (server.address() as AddressInfo).port;
  }

  it('streams a request to the session host and its answer back', async () => {
    const seen: Array<{ url?: string; method?: string; auth?: string; body: string }> = [];
    upstream = createServer((req, res) => {
      let body = '';
      req.on('data', (chunk) => (body += chunk));
      req.on('end', () => {
        seen.push({ url: req.url, method: req.method, auth: req.headers.authorization, body });
        res.setHeader('content-type', 'application/json');
        res.end('{"files":[]}');
      });
    });
    const upstreamPort = await listen(upstream);
    const realFetch = fetch;
    vi.stubGlobal('fetch', (input: URL, init: RequestInit) =>
      realFetch(input.href.replace(`https://${HOST}`, `http://127.0.0.1:${upstreamPort}`), init),
    );
    const handler = forwardSessionHost(DOMAIN);
    local = createServer((req, res) => handler(req, res, () => res.end('next')));
    const base = `http://127.0.0.1:${await listen(local)}`;

    const got = await realFetch(`${base}/__session-host/${HOST}/s/abc/api/diff/files?base=x`, {
      headers: { authorization: 'Bearer t' },
    });
    expect(got.status).toBe(200);
    expect(got.headers.get('content-type')).toBe('application/json');
    expect(await got.json()).toEqual({ files: [] });

    await realFetch(`${base}/__session-host/${HOST}/s/abc/api/files`, {
      method: 'POST',
      body: 'hello',
    });
    expect(seen).toEqual([
      { url: '/s/abc/api/diff/files?base=x', method: 'GET', auth: 'Bearer t', body: '' },
      { url: '/s/abc/api/files', method: 'POST', auth: undefined, body: 'hello' },
    ]);

    expect(await (await realFetch(`${base}/other`)).text()).toBe('next');
    const refused = await realFetch(`${base}/__session-host/evil.example.com/s/abc`);
    expect(refused.status).toBe(403);
  });

  it('answers 502 naming the host when the session host cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('ECONNREFUSED')));
    const handler = forwardSessionHost(DOMAIN);
    const response = { statusCode: 0, end: vi.fn(), setHeader: vi.fn() };
    handler(
      { url: `/__session-host/${HOST}/s/abc/api/x`, method: 'GET', headers: {} } as never,
      response as never,
      vi.fn(),
    );
    await vi.waitFor(() => expect(response.end).toHaveBeenCalled());
    expect(response.statusCode).toBe(502);
    expect(response.end.mock.calls[0]![0]).toContain(`https://${HOST} failed: Error: ECONNREFUSED`);
  });
});
