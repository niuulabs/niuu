import { describe, expect, it } from 'vitest';
import { deriveTerminalWsUrl, normalizeSessionUrl, wsUrlToHttpBase } from './liveSessionTransport';

describe('liveSessionTransport', () => {
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

  it('returns null for malformed urls', () => {
    expect(wsUrlToHttpBase('not-a-url')).toBeNull();
    expect(deriveTerminalWsUrl('not-a-url')).toBeNull();
  });
});
