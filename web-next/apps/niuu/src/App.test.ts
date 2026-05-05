import { describe, expect, it, vi } from 'vitest';
import { publishServiceBackends, resolveConfigEndpoint, shouldBypassAuthGate } from './App';

function makeStorage(initial: Record<string, string> = {}) {
  const state = new Map(Object.entries(initial));
  return {
    getItem: vi.fn((key: string) => state.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      state.set(key, value);
    }),
    removeItem: vi.fn((key: string) => {
      state.delete(key);
    }),
  };
}

describe('resolveConfigEndpoint', () => {
  it('defaults to /config.json when there is no override', () => {
    const storage = makeStorage();
    expect(resolveConfigEndpoint({ search: '' }, storage)).toBe('/config.json');
  });

  it('accepts a query-string override and persists it', () => {
    const storage = makeStorage();
    expect(resolveConfigEndpoint({ search: '?config=/config.live.json' }, storage)).toBe(
      '/config.live.json',
    );
    expect(storage.setItem).toHaveBeenCalledWith('niuu.config.endpoint', '/config.live.json');
  });

  it('reuses a persisted override when no query-string override is present', () => {
    const storage = makeStorage({ 'niuu.config.endpoint': '/config.live.json' });
    expect(resolveConfigEndpoint({ search: '' }, storage)).toBe('/config.live.json');
  });

  it('resets back to the default endpoint when requested', () => {
    const storage = makeStorage({ 'niuu.config.endpoint': '/config.live.json' });
    expect(resolveConfigEndpoint({ search: '?config=default' }, storage)).toBe('/config.json');
    expect(storage.removeItem).toHaveBeenCalledWith('niuu.config.endpoint');
  });

  it('rejects protocol-relative overrides', () => {
    const storage = makeStorage();

    expect(resolveConfigEndpoint({ search: '?config=//evil.test/config.json' }, storage)).toBe(
      '/config.json',
    );
    expect(storage.setItem).not.toHaveBeenCalled();
  });
});

describe('publishServiceBackends', () => {
  it('stores the resolved backend map on the provided target', () => {
    const target: Record<string, unknown> = {};
    const backends = { forge: { mode: 'live' } };

    publishServiceBackends(backends, target);

    expect(target.__NIUU_SERVICE_BACKENDS__).toBe(backends);
  });
});

describe('shouldBypassAuthGate', () => {
  it('allows the login entrypoints through without auth', () => {
    expect(shouldBypassAuthGate('/login')).toBe(true);
    expect(shouldBypassAuthGate('/login/callback')).toBe(true);
    expect(shouldBypassAuthGate('/login/callback/provider')).toBe(true);
  });

  it('protects non-login routes', () => {
    expect(shouldBypassAuthGate('/')).toBe(false);
    expect(shouldBypassAuthGate('/volundr')).toBe(false);
    expect(shouldBypassAuthGate('/tyr/dispatch')).toBe(false);
  });
});
