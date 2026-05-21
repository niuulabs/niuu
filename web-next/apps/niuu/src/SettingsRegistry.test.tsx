import { describe, expect, it } from 'vitest';

import { buildMountedSettingsProviders } from './SettingsRegistry';

describe('buildMountedSettingsProviders', () => {
  it('includes remote providers for enabled plugins', () => {
    const providers = buildMountedSettingsProviders({
      theme: 'ice',
      plugins: {
        login: { enabled: true, order: 0 },
        credentials: { enabled: true, order: 1 },
        integrations: { enabled: true, order: 2 },
        ting: { enabled: true, order: 3 },
        bifrost: { enabled: true, order: 4 },
        ravn: { enabled: true, order: 5 },
      },
      services: {
        identity: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1' },
        credentials: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/credentials' },
        integrations: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/integrations' },
        ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
        bifrost: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/bifrost' },
        ravn: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn' },
      },
    });

    expect(providers.map((provider) => provider.id)).toEqual([
      'identity',
      'credentials',
      'integrations',
      'ting',
      'bifrost',
      'ravn',
    ]);
    expect(providers[0]).toMatchObject({
      source: 'remote',
      id: 'identity',
      baseUrl: 'http://localhost:8080/api/v1/identity',
    });
    expect(providers[1]).toMatchObject({
      source: 'remote',
      id: 'credentials',
      baseUrl: 'http://localhost:8080/api/v1/credentials',
    });
    expect(providers[2]).toMatchObject({
      source: 'remote',
      id: 'integrations',
      baseUrl: 'http://localhost:8080/api/v1/integrations',
    });
    expect(providers[3]).toMatchObject({
      source: 'remote',
      id: 'ting',
      baseUrl: 'http://localhost:8080/api/v1/ting',
    });
    expect(providers[4]).toMatchObject({
      source: 'remote',
      id: 'bifrost',
      baseUrl: 'http://localhost:8080/api/v1/bifrost',
    });
    expect(providers[5]).toMatchObject({
      source: 'remote',
      id: 'ravn',
      baseUrl: 'http://localhost:8080/api/v1/ravn',
    });
  });

  it('omits disabled plugin providers', () => {
    const providers = buildMountedSettingsProviders({
      theme: 'ice',
      plugins: {
        ting: { enabled: false, order: 2 },
        ravn: { enabled: false, order: 4 },
      },
      services: {},
    });

    expect(providers).toEqual([]);
  });
});
