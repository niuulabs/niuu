import { describe, it, expect } from 'vitest';
import { niuuConfigSchema } from './config';

describe('niuuConfigSchema', () => {
  it('fills sensible defaults from an empty object', () => {
    const parsed = niuuConfigSchema.parse({});
    expect(parsed.theme).toBe('ice');
    expect(parsed.plugins).toEqual({});
    expect(parsed.services).toEqual({});
  });

  it('accepts a full config', () => {
    const parsed = niuuConfigSchema.parse({
      theme: 'ice',
      plugins: {
        ting: { enabled: true, order: 4 },
        volundr: { enabled: false, order: 5, reason: 'k8s not provisioned' },
      },
      services: {
        ting: { baseUrl: 'https://api.niuu.world/ting', mode: 'http' },
      },
    });
    expect(parsed.plugins.ting?.enabled).toBe(true);
    expect(parsed.plugins.volundr?.enabled).toBe(false);
    expect(parsed.services.ting?.baseUrl).toBe('https://api.niuu.world/ting');
  });

  it('accepts root-relative service URLs', () => {
    const parsed = niuuConfigSchema.parse({
      services: {
        bifrost: { baseUrl: '/api/v1/bifrost', mode: 'http' },
        mimir: { baseUrl: '/api/v1/mimir', mode: 'http' },
        'forge.pty': { wsUrl: '/s/{sessionId}/session', mode: 'ws' },
      },
    });
    expect(parsed.services.bifrost?.baseUrl).toBe('/api/v1/bifrost');
    expect(parsed.services.mimir?.baseUrl).toBe('/api/v1/mimir');
    expect(parsed.services['forge.pty']?.wsUrl).toBe('/s/{sessionId}/session');
  });

  it('accepts absolute websocket service URLs', () => {
    const parsed = niuuConfigSchema.parse({
      services: {
        'forge.pty': { wsUrl: 'wss://niuu.example.com/s/{sessionId}/session', mode: 'ws' },
      },
    });
    expect(parsed.services['forge.pty']?.wsUrl).toBe(
      'wss://niuu.example.com/s/{sessionId}/session',
    );
  });

  it('rejects an invalid theme', () => {
    expect(() => niuuConfigSchema.parse({ theme: 'ultraviolet' })).toThrow();
  });

  it('rejects an invalid service URL', () => {
    expect(() =>
      niuuConfigSchema.parse({
        services: {
          bifrost: { baseUrl: 'not-a-url', mode: 'http' },
        },
      }),
    ).toThrow('Invalid url');
  });

  it('rejects a non-websocket streaming URL', () => {
    expect(() =>
      niuuConfigSchema.parse({
        services: {
          'forge.pty': { wsUrl: 'https://niuu.example.com/s/{sessionId}/session', mode: 'ws' },
        },
      }),
    ).toThrow('Invalid websocket url');
  });

  it('rejects an invalid websocket service URL', () => {
    expect(() =>
      niuuConfigSchema.parse({
        services: {
          'forge.pty': { wsUrl: 'not-a-websocket-url', mode: 'ws' },
        },
      }),
    ).toThrow('Invalid websocket url');
  });
});
