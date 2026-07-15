import { describe, expect, it } from 'vitest';
import { deriveTerminalWsUrl, normalizeSessionUrl, wsUrlToHttpBase } from './transport';

describe('chat transport', () => {
  it('derives an http base from a chat websocket', () => {
    expect(wsUrlToHttpBase('wss://api.example.com/s/abc/session')).toBe(
      'https://api.example.com/s/abc',
    );
  });

  it('supports legacy api/session suffixes', () => {
    expect(wsUrlToHttpBase('ws://localhost:8080/s/abc/api/session')).toBe(
      'http://localhost:8080/s/abc',
    );
  });

  it('derives the terminal websocket from the chat websocket', () => {
    expect(deriveTerminalWsUrl('ws://localhost:8080/s/abc/session')).toBe(
      'ws://localhost:8080/s/abc/terminal/ws',
    );
  });

  it('preserves websocket schemes when normalizing loopback hosts', () => {
    const originalWindow = globalThis.window;
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: { location: { origin: 'http://localhost:8080' } },
    });

    expect(normalizeSessionUrl('ws://127.0.0.1:8080/s/abc/session')).toBe(
      'ws://localhost:8080/s/abc/session',
    );

    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: originalWindow,
    });
  });

  it('resolves same-origin session proxy paths as websockets', () => {
    const originalWindow = globalThis.window;
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: { location: { origin: 'https://yggdrasil.niuu.world' } },
    });

    expect(normalizeSessionUrl('/s/session-1/session')).toBe(
      'wss://yggdrasil.niuu.world/s/session-1/session',
    );

    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: originalWindow,
    });
  });

  it('returns null for malformed urls', () => {
    expect(wsUrlToHttpBase('not-a-url')).toBeNull();
    expect(deriveTerminalWsUrl('not-a-url')).toBeNull();
  });

  it('handles absent, invalid, and server-side session urls', () => {
    expect(normalizeSessionUrl(null)).toBeNull();
    expect(normalizeSessionUrl('not-a-url')).toBe('not-a-url');
    expect(wsUrlToHttpBase('')).toBeNull();
    expect(deriveTerminalWsUrl(null)).toBeNull();

    const originalWindow = globalThis.window;
    Object.defineProperty(globalThis, 'window', { configurable: true, value: undefined });
    expect(normalizeSessionUrl('/s/server/session')).toBe('/s/server/session');
    expect(normalizeSessionUrl('ws://api.example.test/s/server/session')).toBe(
      'ws://api.example.test/s/server/session',
    );
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: originalWindow,
    });
  });

  it('maps public protocols only for matching loopback endpoints', () => {
    const originalWindow = globalThis.window;
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: { location: { origin: 'https://localhost:8443' } },
    });

    expect(normalizeSessionUrl('ws://127.0.0.1:8443/s/abc/session')).toBe(
      'wss://localhost:8443/s/abc/session',
    );
    expect(normalizeSessionUrl('http://127.0.0.1:8443/s/abc/session')).toBe(
      'https://localhost:8443/s/abc/session',
    );
    expect(normalizeSessionUrl('ftp://127.0.0.1:8443/archive')).toBe(
      'ftp://localhost:8443/archive',
    );
    expect(normalizeSessionUrl('ws://127.0.0.1:9000/s/abc/session')).toBe(
      'ws://127.0.0.1:9000/s/abc/session',
    );
    expect(normalizeSessionUrl('ws://api.example.test:8443/s/abc/session')).toBe(
      'ws://api.example.test:8443/s/abc/session',
    );
    expect(deriveTerminalWsUrl('wss://api.example.test/s/abc/api/session')).toBe(
      'wss://api.example.test/s/abc/terminal/ws',
    );

    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: originalWindow,
    });
  });
});
