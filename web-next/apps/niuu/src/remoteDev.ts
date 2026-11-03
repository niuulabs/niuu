import type { Connect, Plugin, ProxyOptions } from 'vite';

/** The port the cluster's sign-in accepts a localhost redirect on. */
export const REMOTE_DEV_PORT = 5199;

const CONFIG_PATHS = new Set(['/config.json', '/config.live.json']);

interface RemoteConfig {
  services: Record<string, { baseUrl?: string; [key: string]: unknown }>;
  [key: string]: unknown;
}

/**
 * Point every service back at this dev server, so the browser never makes a
 * cross-origin call. A service on another host gets its own proxy route.
 */
export function localizeRemoteConfig<T extends RemoteConfig>(config: T, origin: string) {
  const proxy: Record<string, ProxyOptions> = {};
  const services = Object.fromEntries(
    Object.entries(config.services).map(([name, service]) => {
      if (!service.baseUrl?.startsWith('http')) return [name, service];
      const url = new URL(service.baseUrl);
      if (url.origin !== origin) proxy[url.pathname] = { target: url.origin, changeOrigin: true };
      return [name, { ...service, baseUrl: url.pathname }];
    }),
  );
  return { config: { ...config, services }, proxy };
}

function serveConfig(body: string): Connect.NextHandleFunction {
  return (request, response, next) => {
    if (!CONFIG_PATHS.has(request.url?.split('?')[0] ?? '')) return next();
    response.setHeader('content-type', 'application/json');
    response.end(body);
  };
}

/** Run this UI on a deployed cluster: its config, its APIs and its sign-in. */
export async function remoteDev(target: string) {
  const origin = new URL(target).origin;
  const response = await fetch(`${origin}/config.live.json`);
  if (!response.ok) {
    throw new Error(
      `${origin}/config.live.json answered ${response.status}: NIUU_REMOTE must be a deployed Niuu.`,
    );
  }
  const { config, proxy } = localizeRemoteConfig((await response.json()) as RemoteConfig, origin);
  const handler = serveConfig(JSON.stringify(config));
  const plugin: Plugin = {
    name: 'niuu-remote-config',
    configureServer: (server) => void server.middlewares.use(handler),
    configurePreviewServer: (server) => void server.middlewares.use(handler),
  };
  return { origin, plugin, proxy };
}
