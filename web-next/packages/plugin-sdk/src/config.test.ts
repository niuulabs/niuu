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
        tyr: { enabled: true, order: 4 },
        volundr: { enabled: false, order: 5, reason: 'k8s not provisioned' },
      },
      services: {
        tyr: { baseUrl: 'https://api.niuu.world/tyr', mode: 'http' },
      },
    });
    expect(parsed.plugins.tyr?.enabled).toBe(true);
    expect(parsed.plugins.volundr?.enabled).toBe(false);
    expect(parsed.services.tyr?.baseUrl).toBe('https://api.niuu.world/tyr');
  });

  it('rejects an invalid theme', () => {
    expect(() => niuuConfigSchema.parse({ theme: 'ultraviolet' })).toThrow();
  });
});
