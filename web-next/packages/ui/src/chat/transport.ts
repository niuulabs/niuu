function isLoopbackHostname(hostname: string): boolean {
  return hostname === '127.0.0.1' || hostname === 'localhost';
}

function publicProtocolFor(parsedProtocol: string, currentProtocol: string): string {
  if (parsedProtocol === 'ws:' || parsedProtocol === 'wss:') {
    return currentProtocol === 'https:' ? 'wss:' : 'ws:';
  }
  if (parsedProtocol === 'http:' || parsedProtocol === 'https:') {
    return currentProtocol === 'https:' ? 'https:' : 'http:';
  }
  return parsedProtocol;
}

export function normalizeSessionUrl(url: string | null | undefined): string | null {
  if (!url) return null;

  try {
    const parsed = new URL(url);
    if (typeof window === 'undefined') return parsed.toString();

    const current = new URL(window.location.origin);
    const samePort = parsed.port === current.port;
    if (isLoopbackHostname(parsed.hostname) && isLoopbackHostname(current.hostname) && samePort) {
      parsed.protocol = publicProtocolFor(parsed.protocol, current.protocol);
      parsed.hostname = current.hostname;
      parsed.port = current.port;
    }
    return parsed.toString();
  } catch {
    return url;
  }
}

export function wsUrlToHttpBase(wsUrl: string): string | null {
  try {
    const parsed = new URL(normalizeSessionUrl(wsUrl) ?? wsUrl);
    const protocol = parsed.protocol === 'wss:' ? 'https:' : 'http:';
    const basePath = parsed.pathname.replace(/\/(api\/)?session$/, '');
    return `${protocol}//${parsed.host}${basePath}`;
  } catch {
    return null;
  }
}

export function deriveTerminalWsUrl(chatEndpoint: string | null | undefined): string | null {
  const normalizedUrl = normalizeSessionUrl(chatEndpoint);
  if (!normalizedUrl) return null;

  try {
    const parsed = new URL(normalizedUrl);
    const protocol = parsed.protocol === 'wss:' ? 'wss:' : 'ws:';
    const prefix = parsed.pathname.replace(/\/(api\/)?session$/, '');
    return `${protocol}//${parsed.host}${prefix}/terminal/ws`;
  } catch {
    return null;
  }
}
