import type { IncomingMessage, ServerResponse } from 'node:http';
import { Readable } from 'node:stream';
import type { Connect, Plugin, ProxyOptions } from 'vite';

/** The port the cluster's sign-in accepts a localhost redirect on. */
export const REMOTE_DEV_PORT = 5199;

const CONFIG_PATHS = new Set(['/config.json', '/config.live.json']);

/** Where this dev server forwards a session host's HTTP calls: `/__session-host/<host>/s/…`. */
export const SESSION_HOST_PREFIX = '/__session-host/';

/** Headers that describe one hop, so they are not copied across the forward. */
const HOP_HEADERS = new Set([
  'connection',
  'content-encoding',
  'content-length',
  'host',
  'keep-alive',
  'origin',
  'referer',
  'transfer-encoding',
]);

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

/**
 * The domain a cluster's session hosts live under: the remote's host without its first label,
 * so `yggdrasil.niuu.world` admits `sessions.valhalla.asgard.niuu.world`.
 */
export function sessionDomain(origin: string): string {
  const labels = new URL(origin).hostname.split('.');
  if (labels.length < 3) {
    throw new Error(`NIUU_REMOTE ${origin} has no parent domain to admit session hosts under.`);
  }
  return labels.slice(1).join('.');
}

/** The session-host URL a forwarded path names, or null when it is not one this cluster serves. */
export function sessionHostTarget(path: string, domain: string): URL | null {
  if (!path.startsWith(SESSION_HOST_PREFIX)) return null;
  const rest = path.slice(SESSION_HOST_PREFIX.length);
  const slash = rest.indexOf('/');
  if (slash < 0) return null;
  const host = rest.slice(0, slash);
  if (!host.endsWith(`.${domain}`) || !/^[a-z0-9.-]+$/i.test(host)) return null;
  const target = new URL(`https://${host}${rest.slice(slash)}`);
  return target.pathname.startsWith('/s/') ? target : null;
}

/**
 * A session's own API lives on its session host, which the session record names in full. The
 * browser cannot call it from localhost (the host answers CORS for the cluster's origin only),
 * so this page sends those calls through the dev server instead. WebSockets need no CORS and
 * keep going direct.
 */
export function sessionHostFetchShim(domain: string): string {
  return `(() => {
  const prefix = ${JSON.stringify(SESSION_HOST_PREFIX)};
  const suffix = ${JSON.stringify(`.${domain}`)};
  const direct = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), location.href);
    if (url.protocol !== 'https:' || !url.hostname.endsWith(suffix) || !url.pathname.startsWith('/s/')) {
      return direct(input, init);
    }
    const local = location.origin + prefix + url.host + url.pathname + url.search;
    if (!(input instanceof Request)) return direct(local, init);
    const body = input.method === 'GET' || input.method === 'HEAD' ? undefined : input.arrayBuffer();
    return Promise.resolve(body).then((bytes) =>
      direct(local, { method: input.method, headers: input.headers, body: bytes, signal: input.signal, ...init }),
    );
  };
})();`;
}

function forwardHeaders(source: Iterable<[string, string | string[] | undefined]>) {
  const headers = new Headers();
  for (const [name, value] of source) {
    if (value === undefined || HOP_HEADERS.has(name.toLowerCase())) continue;
    for (const item of Array.isArray(value) ? value : [value]) headers.append(name, item);
  }
  return headers;
}

/** Forward `/__session-host/<host>/s/…` to that session host, streaming both ways. */
export function forwardSessionHost(domain: string): Connect.NextHandleFunction {
  return (request: IncomingMessage, response: ServerResponse, next) => {
    const url = request.url ?? '';
    if (!url.startsWith(SESSION_HOST_PREFIX)) return next();
    const target = sessionHostTarget(url, domain);
    if (!target) {
      response.statusCode = 403;
      response.end(`${url} does not name a session host under ${domain}.`);
      return;
    }
    const method = request.method ?? 'GET';
    const hasBody = method !== 'GET' && method !== 'HEAD';
    void fetch(target, {
      method,
      headers: forwardHeaders(Object.entries(request.headers)),
      body: hasBody ? (Readable.toWeb(request) as ReadableStream) : undefined,
      ...(hasBody ? { duplex: 'half' } : {}),
    } as RequestInit)
      .then((upstream) => {
        response.statusCode = upstream.status;
        upstream.headers.forEach((value, name) => {
          if (!HOP_HEADERS.has(name)) response.setHeader(name, value);
        });
        if (!upstream.body) return void response.end();
        Readable.fromWeb(upstream.body as never).pipe(response);
      })
      .catch((error: unknown) => {
        response.statusCode = 502;
        response.end(`Forwarding to ${target.origin} failed: ${String(error)}`);
      });
  };
}

function serveConfig(body: string): Connect.NextHandleFunction {
  return (request, response, next) => {
    if (!CONFIG_PATHS.has(request.url?.split('?')[0] ?? '')) return next();
    response.setHeader('content-type', 'application/json');
    response.end(body);
  };
}

/** Run this UI on a deployed cluster: its config, its APIs, its session hosts and its sign-in. */
export async function remoteDev(target: string) {
  const origin = new URL(target).origin;
  const domain = sessionDomain(origin);
  const response = await fetch(`${origin}/config.live.json`);
  if (!response.ok) {
    throw new Error(
      `${origin}/config.live.json answered ${response.status}: NIUU_REMOTE must be a deployed Niuu.`,
    );
  }
  const { config, proxy } = localizeRemoteConfig((await response.json()) as RemoteConfig, origin);
  const handler = serveConfig(JSON.stringify(config));
  const sessionHosts = forwardSessionHost(domain);
  const plugin: Plugin = {
    name: 'niuu-remote-config',
    configureServer: (server) => {
      server.middlewares.use(handler);
      server.middlewares.use(sessionHosts);
    },
    configurePreviewServer: (server) => {
      server.middlewares.use(handler);
      server.middlewares.use(sessionHosts);
    },
    transformIndexHtml: () => [
      { tag: 'script', children: sessionHostFetchShim(domain), injectTo: 'head-prepend' },
    ],
  };
  return { origin, plugin, proxy };
}
