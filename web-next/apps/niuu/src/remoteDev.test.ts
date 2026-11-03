import { afterEach, describe, expect, it, vi } from 'vitest';
import { localizeRemoteConfig, remoteDev } from './remoteDev';

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

    let handler: (req: unknown, res: unknown, next: () => void) => void = () => undefined;
    const server = { middlewares: { use: (fn: typeof handler) => (handler = fn) } };
    (plugin.configureServer as (value: unknown) => void)(server);

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
