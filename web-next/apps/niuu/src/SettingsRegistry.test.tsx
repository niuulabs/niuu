import { describe, expect, it, vi } from 'vitest';

vi.mock('@niuulabs/plugin-tyr', () => ({
  tyrMountedSettingsProvider: {
    id: 'tyr',
    pluginId: 'tyr',
    title: 'Tyr',
    subtitle: 'saga coordinator settings',
    scope: 'service',
    defaultSectionId: 'general',
    sections: [
      {
        id: 'general',
        label: 'General',
        description: 'Core service bindings for the saga coordinator',
        render: () => null,
      },
    ],
  },
}));

import { buildMountedSettingsProviders } from './SettingsRegistry';

describe('buildMountedSettingsProviders', () => {
  it('includes local tyr settings and remote providers for enabled plugins', () => {
    const providers = buildMountedSettingsProviders({
      theme: 'ice',
      plugins: {
        login: { enabled: true, order: 0 },
        tyr: { enabled: true, order: 2 },
        ravn: { enabled: true, order: 4 },
      },
      services: {
        identity: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/identity' },
        ravn: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn' },
      },
    });

    expect(providers.map((provider) => provider.id)).toEqual(['identity', 'tyr', 'ravn']);
    expect(providers[0]).toMatchObject({
      source: 'remote',
      id: 'identity',
      baseUrl: 'http://localhost:8080/api/v1/identity',
    });
    expect(providers[1]).toMatchObject({
      source: 'local',
      id: 'tyr',
    });
    expect(providers[2]).toMatchObject({
      source: 'remote',
      id: 'ravn',
      baseUrl: 'http://localhost:8080/api/v1/ravn',
    });
  });

  it('omits disabled plugin providers', () => {
    const providers = buildMountedSettingsProviders({
      theme: 'ice',
      plugins: {
        tyr: { enabled: false, order: 2 },
        ravn: { enabled: false, order: 4 },
      },
      services: {},
    });

    expect(providers).toEqual([]);
  });
});
