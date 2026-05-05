import { beforeEach, describe, expect, it, vi } from 'vitest';

const queryMocks = vi.hoisted(() => ({
  createApiClient: vi.fn((basePath: string) => ({ basePath })),
}));

const pluginSdkMocks = vi.hoisted(() => ({
  buildFeatureCatalogAdapter: vi.fn((client) => ({ kind: 'feature-catalog', client })),
  createMockFeatureCatalogService: vi.fn(() => ({ kind: 'mock-feature-catalog' })),
  buildIdentityAdapter: vi.fn((client) => ({ kind: 'identity', client })),
  createMockIdentityService: vi.fn(() => ({ kind: 'mock-identity' })),
}));

const tyrMocks = vi.hoisted(() => ({
  createMockTyrService: vi.fn(() => ({ kind: 'mock-tyr' })),
  createMockDispatcherService: vi.fn(() => ({ kind: 'mock-dispatcher' })),
  createMockTyrSessionService: vi.fn(() => ({ kind: 'mock-sessions' })),
  createMockTrackerService: vi.fn(() => ({ kind: 'mock-tracker' })),
  createMockWorkflowService: vi.fn(() => ({ kind: 'mock-workflows' })),
  createMockDispatchBus: vi.fn(() => ({ kind: 'mock-dispatch' })),
  createMockTyrSettingsService: vi.fn(() => ({ kind: 'mock-settings' })),
  createMockAuditLogService: vi.fn(() => ({ kind: 'mock-audit' })),
  buildTyrHttpAdapter: vi.fn((client) => ({ kind: 'tyr', client })),
  buildDispatcherHttpAdapter: vi.fn((client) => ({ kind: 'dispatcher', client })),
  buildTyrSessionHttpAdapter: vi.fn((client) => ({ kind: 'sessions', client })),
  buildTrackerHttpAdapter: vi.fn((client) => ({ kind: 'tracker', client })),
  buildWorkflowHttpAdapter: vi.fn((client) => ({ kind: 'workflows', client })),
  buildDispatchBusHttpAdapter: vi.fn((client) => ({ kind: 'dispatch', client })),
  buildTyrSettingsHttpAdapter: vi.fn((client) => ({ kind: 'settings', client })),
  buildTyrAuditLogHttpAdapter: vi.fn((client) => ({ kind: 'audit', client })),
}));

const ravnMocks = vi.hoisted(() => ({
  createMockPersonaStore: vi.fn(() => ({})),
  createMockRavenStream: vi.fn(() => ({})),
  createMockSessionStream: vi.fn(() => ({})),
  createMockTriggerStore: vi.fn(() => ({})),
  createMockBudgetStream: vi.fn(() => ({})),
  buildRavnPersonaAdapter: vi.fn(() => ({})),
  buildRavnRavenAdapter: vi.fn(() => ({})),
  buildRavnSessionAdapter: vi.fn(() => ({})),
  buildRavnTriggerAdapter: vi.fn(() => ({})),
  buildRavnBudgetAdapter: vi.fn(() => ({})),
}));

const observatoryMocks = vi.hoisted(() => ({
  createMockRegistryRepository: vi.fn(() => ({})),
  createMockTopologyStream: vi.fn(() => ({})),
  createMockEventStream: vi.fn(() => ({})),
  buildObservatoryRegistryHttpAdapter: vi.fn(() => ({})),
  buildObservatoryTopologySseStream: vi.fn(() => ({})),
  buildObservatoryEventsSseStream: vi.fn(() => ({})),
}));

const volundrMocks = vi.hoisted(() => ({
  createMockVolundrService: vi.fn(() => ({ kind: 'mock-volundr' })),
  createMockClusterAdapter: vi.fn(() => ({ kind: 'mock-clusters' })),
  createMockTemplateStore: vi.fn(() => ({ kind: 'mock-templates' })),
  createMockSessionStore: vi.fn(() => ({ kind: 'mock-session-store' })),
  buildVolundrHttpAdapter: vi.fn((client) => ({
    kind: 'volundr',
    client,
    getSessions: vi.fn().mockResolvedValue([]),
    getSession: vi.fn().mockResolvedValue(null),
    getClusterResources: vi.fn().mockResolvedValue({ resourceTypes: [], nodes: [] }),
    getTemplates: vi.fn().mockResolvedValue([]),
    getTemplate: vi.fn().mockResolvedValue(null),
    listArchivedSessions: vi.fn().mockResolvedValue([]),
    deleteSession: vi.fn().mockResolvedValue(undefined),
    subscribe: vi.fn(() => () => {}),
  })),
  createMockPtyStream: vi.fn(() => ({})),
  createMockMetricsStream: vi.fn(() => ({})),
  createMockFileSystemPort: vi.fn(() => ({})),
  buildVolundrFileSystemHttpAdapter: vi.fn((options) => ({ kind: 'filesystem', options })),
  buildVolundrPtyWsAdapter: vi.fn(() => ({})),
  buildVolundrMetricsSseAdapter: vi.fn(() => ({})),
}));

vi.mock('@niuulabs/query', () => ({
  createApiClient: queryMocks.createApiClient,
}));
vi.mock('@niuulabs/plugin-sdk', () => pluginSdkMocks);

vi.mock('@niuulabs/plugin-tyr', () => tyrMocks);
vi.mock('@niuulabs/plugin-ravn', () => ravnMocks);
vi.mock('@niuulabs/plugin-mimir', () => ({
  createMimirMockAdapter: vi.fn(() => ({})),
  buildMimirHttpAdapter: vi.fn(() => ({})),
}));
vi.mock('@niuulabs/plugin-observatory', () => observatoryMocks);
vi.mock('@niuulabs/plugin-volundr', () => volundrMocks);

import {
  buildServices,
  buildServiceBackendStatus,
  resolveCanonicalServiceBase,
  resolveForgeServiceBase,
  buildSharedFeatureCatalogService,
  buildSharedIdentityService,
  resolveSharedApiBase,
  toSharedApiBase,
  toHostBase,
  toHostPtyWsUrl,
  resolveSettingsServiceBase,
} from './services';

describe('toSharedApiBase', () => {
  it('strips a trailing Tyr service suffix', () => {
    expect(toSharedApiBase('http://localhost:8080/api/v1/tyr')).toBe(
      'http://localhost:8080/api/v1',
    );
  });

  it('strips a trailing Volundr service suffix', () => {
    expect(toSharedApiBase('http://localhost:8080/api/v1/volundr')).toBe(
      'http://localhost:8080/api/v1',
    );
  });

  it('strips a trailing Forge service suffix', () => {
    expect(toSharedApiBase('http://localhost:8080/api/v1/forge')).toBe(
      'http://localhost:8080/api/v1',
    );
  });
});

describe('toHostBase', () => {
  it('strips a trailing canonical Forge service suffix', () => {
    expect(toHostBase('http://localhost:8080/api/v1/forge')).toBe('http://localhost:8080');
  });

  it('strips a trailing legacy Volundr service suffix', () => {
    expect(toHostBase('http://localhost:8080/api/v1/volundr')).toBe('http://localhost:8080');
  });
});

describe('toHostPtyWsUrl', () => {
  it('derives the bundled host websocket PTY route from Forge', () => {
    expect(toHostPtyWsUrl('http://localhost:8080/api/v1/forge')).toBe(
      'ws://localhost:8080/s/{sessionId}/session',
    );
  });
});

describe('resolveSharedApiBase', () => {
  it('prefers the Tyr shared base when Tyr is live', () => {
    expect(
      resolveSharedApiBase({
        services: {
          tyr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/tyr' },
          volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
        },
      } as any),
    ).toBe('http://localhost:8080/api/v1');
  });

  it('falls back to the canonical Forge shared base when Tyr is not live', () => {
    expect(
      resolveSharedApiBase({
        services: {
          forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
        },
      } as any),
    ).toBe('http://localhost:8080/api/v1');
  });

  it('falls back to the Volundr shared base when Tyr is not live', () => {
    expect(
      resolveSharedApiBase({
        services: {
          volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
        },
      } as any),
    ).toBe('http://localhost:8080/api/v1');
  });

  it('returns null when neither Tyr nor Volundr is live', () => {
    expect(
      resolveSharedApiBase({
        services: {
          tyr: { mode: 'mock' },
          volundr: { mode: 'mock' },
        },
      } as any),
    ).toBeNull();
  });
});

describe('resolveCanonicalServiceBase', () => {
  it('prefers an explicit canonical domain base when configured', () => {
    expect(
      resolveCanonicalServiceBase(
        {
          services: {
            features: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/features' },
            tyr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/tyr' },
          },
        } as any,
        'features',
      ),
    ).toBe('http://localhost:8080/api/v1/features');
  });

  it('falls back to the shared base when the canonical domain is not explicitly configured', () => {
    expect(
      resolveCanonicalServiceBase(
        {
          services: {
            volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
          },
        } as any,
        'identity',
      ),
    ).toBe('http://localhost:8080/api/v1');
  });

  it('returns null when neither an explicit nor shared live base exists', () => {
    expect(
      resolveCanonicalServiceBase(
        {
          services: {
            identity: { mode: 'mock' },
            volundr: { mode: 'mock' },
          },
        } as any,
        'identity',
      ),
    ).toBeNull();
  });
});

describe('resolveSettingsServiceBase', () => {
  it('resolves identity settings from the canonical identity base', () => {
    expect(
      resolveSettingsServiceBase(
        {
          services: {
            identity: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/identity' },
          },
        } as any,
        'identity',
      ),
    ).toBe('http://localhost:8080/api/v1/identity');
  });

  it('resolves tyr settings from the normalized tyr base', () => {
    expect(
      resolveSettingsServiceBase(
        {
          services: {
            'tyr.settings': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/tyr/settings' },
          },
        } as any,
        'tyr',
      ),
    ).toBe('http://localhost:8080/api/v1/tyr');
  });

  it('resolves ravn settings from the grouped ravn base', () => {
    expect(
      resolveSettingsServiceBase(
        {
          services: {
            ravn: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn' },
          },
        } as any,
        'ravn',
      ),
    ).toBe('http://localhost:8080/api/v1/ravn');
  });
});

describe('resolveForgeServiceBase', () => {
  it('prefers an explicit forge domain base over the legacy volundr key', () => {
    expect(
      resolveForgeServiceBase({
        services: {
          forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
          volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
        },
      } as any),
    ).toBe('http://localhost:8080/api/v1/forge');
  });

  it('falls back to the legacy volundr base when no explicit forge base exists', () => {
    expect(
      resolveForgeServiceBase({
        services: {
          volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
        },
      } as any),
    ).toBe('http://localhost:8080/api/v1/volundr');
  });

  it('returns null when neither forge nor volundr is live', () => {
    expect(
      resolveForgeServiceBase({
        services: {
          forge: { mode: 'mock' },
          volundr: { mode: 'mock' },
        },
      } as any),
    ).toBeNull();
  });
});

describe('buildServices live base selection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('prefers an explicit volundr base for the full volundr adapter', () => {
    buildServices({
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
        volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
      },
    } as any);

    expect(volundrMocks.buildVolundrHttpAdapter).toHaveBeenCalledWith(
      expect.objectContaining({
        basePath: 'http://localhost:8080/api/v1/volundr',
      }),
    );
  });

  it('normalizes explicit Tyr sub-service bases back to /api/v1/tyr', () => {
    buildServices({
      services: {
        tyr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/tyr' },
        'tyr.dispatcher': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/tyr/dispatcher',
        },
        'tyr.sessions': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/tyr/sessions',
        },
        'tyr.dispatch': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/tyr/dispatch',
        },
        'tyr.settings': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/tyr/settings',
        },
      },
    } as any);

    expect(tyrMocks.buildDispatcherHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/tyr',
    });
    expect(tyrMocks.buildTyrSessionHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/tyr',
    });
    expect(tyrMocks.buildDispatchBusHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/tyr',
    });
    expect(tyrMocks.buildTyrSettingsHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/tyr',
    });
  });
});

describe('shared domain helpers', () => {
  it('builds shared feature catalog and identity services from the canonical shared base', () => {
    const config = {
      services: {
        tyr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/tyr' },
      },
    } as any;

    expect(buildSharedFeatureCatalogService(config)).toEqual({
      kind: 'feature-catalog',
      client: { basePath: 'http://localhost:8080/api/v1' },
    });
    expect(buildSharedIdentityService(config)).toEqual({
      kind: 'identity',
      client: { basePath: 'http://localhost:8080/api/v1' },
    });
    expect(pluginSdkMocks.buildFeatureCatalogAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(pluginSdkMocks.buildIdentityAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
  });

  it('prefers explicit identity and feature domain configs over the derived shared base', () => {
    const config = {
      services: {
        features: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/features' },
        identity: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/identity' },
        tyr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/tyr' },
      },
    } as any;

    expect(buildSharedFeatureCatalogService(config)).toEqual({
      kind: 'feature-catalog',
      client: { basePath: 'http://localhost:8080/api/v1/features' },
    });
    expect(buildSharedIdentityService(config)).toEqual({
      kind: 'identity',
      client: { basePath: 'http://localhost:8080/api/v1/identity' },
    });
  });

  it('falls back to mock shared services when no shared api base exists', () => {
    const config = {
      services: {
        tyr: { mode: 'mock' },
        volundr: { mode: 'mock' },
      },
    } as any;

    expect(buildSharedFeatureCatalogService(config)).toEqual({ kind: 'mock-feature-catalog' });
    expect(buildSharedIdentityService(config)).toEqual({ kind: 'mock-identity' });
  });
});

describe('buildServiceBackendStatus', () => {
  it('reports explicit and derived live backends separately', () => {
    const status = buildServiceBackendStatus({
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
        'forge.metrics': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge/metrics' },
        'forge.pty': { mode: 'ws', wsUrl: 'ws://localhost:8080/api/v1/forge/pty/{sessionId}' },
        observatory: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/observatory' },
        ravn: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn' },
      },
    } as any);

    expect(status.forge).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1/forge',
      source: 'forge',
    });
    expect(status['forge.metrics']).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1/forge/metrics',
      source: 'forge.metrics',
    });
    expect(status['forge.pty']).toEqual({
      mode: 'live',
      transport: 'ws',
      target: 'ws://localhost:8080/api/v1/forge/pty/{sessionId}',
      source: 'forge.pty',
    });
    expect(status['observatory.registry']).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1/observatory',
      source: 'observatory',
    });
    expect(status['observatory.topology']).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1/observatory/topology',
      source: 'observatory',
    });
    expect(status['ravn.personas']).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1',
      source: 'shared-api',
    });
  });

  it('derives a live forge pty websocket backend from the forge host when available', () => {
    const status = buildServiceBackendStatus({
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
      },
    } as any);

    expect(status['forge.pty']).toEqual({
      mode: 'live',
      transport: 'ws',
      target: 'ws://localhost:8080/s/{sessionId}/session',
      source: 'forge',
    });
  });

  it('reports mock-only workflow and filesystem surfaces explicitly', () => {
    const status = buildServiceBackendStatus({ services: {} } as any);

    expect(status['tyr.workflows']).toEqual({
      mode: 'mock',
      transport: 'mock',
      target: null,
      source: 'mock',
    });
    expect(status.filesystem).toEqual({
      mode: 'mock',
      transport: 'mock',
      target: null,
      source: 'mock',
      note: 'No live filesystem API is wired yet.',
    });
  });

  it('derives a live filesystem backend from the forge host when available', () => {
    const status = buildServiceBackendStatus({
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
      },
    } as any);

    expect(status.filesystem).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080',
      source: 'forge-host',
    });
  });

  it('normalizes explicit ravn persona bases back to the shared /api/v1 root', () => {
    const status = buildServiceBackendStatus({
      services: {
        'ravn.personas': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn/personas' },
        'ravn.ravens': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn/ravens' },
        'ravn.sessions': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn/sessions' },
        'ravn.triggers': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn/triggers' },
        'ravn.budget': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn/budget' },
      },
    } as any);

    expect(status['ravn.personas']).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1',
      source: 'ravn.personas',
    });
    expect(status['ravn.ravens']).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1/ravn',
      source: 'ravn.ravens',
    });
    expect(status['ravn.sessions']).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1/ravn',
      source: 'ravn.sessions',
    });
    expect(status['ravn.triggers']).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1/ravn',
      source: 'ravn.triggers',
    });
    expect(status['ravn.budget']).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1/ravn',
      source: 'ravn.budget',
    });
  });

  it('prefers an explicit personas service base for ravn.personas when configured', () => {
    const status = buildServiceBackendStatus({
      services: {
        personas: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/personas' },
      },
    } as any);

    expect(status['ravn.personas']).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1',
      source: 'personas',
    });
  });

  it('resolves niuu.repos against the shared niuu route instead of forge or volundr', () => {
    const status = buildServiceBackendStatus({
      services: {
        tyr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/tyr' },
      },
    } as any);

    expect(status['niuu.repos']).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1/niuu',
      source: 'shared-api',
    });
  });
});

describe('buildServices', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('builds Tyr tracker and audit services against the shared api base', () => {
    const services = buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        tyr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/tyr' },
      },
    } as any);

    expect(tyrMocks.buildTyrHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/tyr',
    });
    expect(tyrMocks.buildTrackerHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(tyrMocks.buildTyrAuditLogHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(services.features).toEqual({
      kind: 'feature-catalog',
      client: { basePath: 'http://localhost:8080/api/v1' },
    });
    expect(services.identity).toEqual({
      kind: 'identity',
      client: { basePath: 'http://localhost:8080/api/v1' },
    });
    expect((services['tyr.tracker'] as any).kind).toBe('tracker');
    expect((services['tyr.audit'] as any).kind).toBe('audit');
  });

  it('prefers explicit tracker and audit domain configs over the derived shared base', () => {
    buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        tracker: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/tracker' },
        audit: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/audit' },
        tyr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/tyr' },
      },
    } as any);

    expect(tyrMocks.buildTrackerHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/tracker',
    });
    expect(tyrMocks.buildTyrAuditLogHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/audit',
    });
  });

  it('normalizes explicit Tyr subdomain configs back to the shared Tyr base', () => {
    buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        tyr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/tyr' },
        'tyr.dispatcher': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/tyr/dispatcher',
        },
        'tyr.sessions': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/tyr/sessions',
        },
        'tyr.dispatch': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/tyr/dispatch',
        },
        'tyr.settings': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/tyr/settings',
        },
        'tyr.workflows': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/tyr/workflows',
        },
      },
    } as any);

    expect(tyrMocks.buildDispatcherHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/tyr',
    });
    expect(tyrMocks.buildTyrSessionHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/tyr',
    });
    expect(tyrMocks.buildDispatchBusHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/tyr',
    });
    expect(tyrMocks.buildTyrSettingsHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/tyr',
    });
    expect(tyrMocks.buildWorkflowHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/tyr',
    });
  });

  it('falls back to the Volundr host when Tyr is not live', () => {
    buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
      },
    } as any);

    expect(tyrMocks.buildTrackerHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(tyrMocks.buildTyrAuditLogHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
  });

  it('falls back to the Forge host when only the canonical Forge base is live', () => {
    buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
      },
    } as any);

    expect(tyrMocks.buildTrackerHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(tyrMocks.buildTyrAuditLogHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(pluginSdkMocks.buildFeatureCatalogAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(pluginSdkMocks.buildIdentityAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(volundrMocks.buildVolundrFileSystemHttpAdapter).toHaveBeenCalledWith({
      baseUrl: 'http://localhost:8080',
    });
  });

  it('builds niuu.repos against the shared repo catalog route', () => {
    const services = buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        tyr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/tyr' },
      },
    } as any);

    expect(queryMocks.createApiClient).toHaveBeenCalledWith('http://localhost:8080/api/v1/niuu');
    expect(services['niuu.repos']).toBeDefined();
  });

  it('prefers an explicit filesystem base over the derived forge host', () => {
    const services = buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
        filesystem: { mode: 'http', baseUrl: 'http://localhost:9999' },
      },
    } as any);

    expect(volundrMocks.buildVolundrFileSystemHttpAdapter).toHaveBeenCalledWith({
      baseUrl: 'http://localhost:9999',
    });
    expect((services.filesystem as any).kind).toBe('filesystem');
  });

  it('prefers explicit Ravn domain configs over the shared ravn base', () => {
    buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        ravn: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn' },
        'ravn.personas': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn/personas' },
        'ravn.sessions': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn/sessions' },
        'ravn.ravens': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn/ravens' },
        'ravn.triggers': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn/triggers' },
        'ravn.budget': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn/budget' },
      },
    } as any);

    expect(ravnMocks.buildRavnPersonaAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(ravnMocks.buildRavnSessionAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ravn',
    });
    expect(ravnMocks.buildRavnRavenAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ravn',
    });
    expect(ravnMocks.buildRavnTriggerAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ravn',
    });
    expect(ravnMocks.buildRavnBudgetAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ravn',
    });
  });

  it('uses the explicit personas service base for the persona adapter when present', () => {
    buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        personas: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/personas' },
      },
    } as any);

    expect(ravnMocks.buildRavnPersonaAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
  });

  it('builds a live session store from the live Volundr service', async () => {
    const activeSession = {
      id: 'sess-live',
      name: 'feat/canonical-routes',
      source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'feat/canonical-routes' },
      status: 'running',
      model: 'claude-sonnet',
      lastActive: Date.parse('2026-04-24T12:30:00Z'),
      messageCount: 12,
      tokensUsed: 4200,
      taskType: 'forge-web',
      trackerIssue: { identifier: 'NIU-754' },
      activityState: 'active',
    };
    const archivedSession = {
      id: 'sess-archived',
      name: 'fix/legacy-shim-cleanup',
      source: {
        type: 'git',
        repo: 'github.com/niuulabs/volundr',
        branch: 'fix/legacy-shim-cleanup',
      },
      status: 'archived',
      model: 'claude-haiku',
      lastActive: Date.parse('2026-04-23T12:30:00Z'),
      messageCount: 4,
      tokensUsed: 800,
      taskType: 'forge-web',
      trackerIssue: { identifier: 'NIU-753' },
      activityState: null,
      archivedAt: new Date('2026-04-23T13:00:00Z'),
    };
    const liveVolundr = {
      kind: 'volundr',
      getSessions: vi.fn().mockResolvedValue([activeSession]),
      getSession: vi.fn().mockImplementation(async (id: string) => {
        if (id === activeSession.id) return activeSession;
        return null;
      }),
      listArchivedSessions: vi.fn().mockResolvedValue([archivedSession]),
      deleteSession: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn((callback: (sessions: (typeof activeSession)[]) => void) => {
        callback([activeSession]);
        return () => {};
      }),
    };
    volundrMocks.buildVolundrHttpAdapter.mockReturnValue(liveVolundr as any);

    const services = buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
      },
    } as any);

    expect(services['volundr.sessions']).toBe(services.sessionStore);

    const sessionStore = services.sessionStore as any;
    await expect(sessionStore.listSessions()).resolves.toEqual([
      expect.objectContaining({
        id: 'sess-live',
        ravnId: 'NIU-754',
        personaName: 'feat/canonical-routes',
        templateId: 'forge-web',
        clusterId: 'shared',
        state: 'running',
        tokensIn: 4200,
        tokensOut: 0,
        preview: 'github.com/niuulabs/volundr#feat/canonical-routes',
      }),
      expect.objectContaining({
        id: 'sess-archived',
        ravnId: 'NIU-753',
        state: 'terminated',
        terminatedAt: '2026-04-23T13:00:00.000Z',
      }),
    ]);
    await expect(sessionStore.listSessions({ state: 'terminated' })).resolves.toEqual([
      expect.objectContaining({ id: 'sess-archived' }),
    ]);
    await expect(sessionStore.getSession('sess-archived')).resolves.toEqual(
      expect.objectContaining({ id: 'sess-archived', state: 'terminated' }),
    );
    await sessionStore.deleteSession('sess-live');
    expect(liveVolundr.deleteSession).toHaveBeenCalledWith('sess-live');
  });

  it('prefers an explicit forge service base for the main Volundr http adapter', () => {
    buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
        volundr: { mode: 'mock' },
      },
    } as any);

    expect(volundrMocks.buildVolundrHttpAdapter).toHaveBeenCalledWith(
      expect.objectContaining({
        basePath: 'http://localhost:8080/api/v1/forge',
      }),
    );
  });

  it('builds a live template store from the live Volundr service', async () => {
    const liveTemplate = {
      name: 'niuu-platform',
      description: 'Full niuu monorepo',
      repos: [
        { url: 'https://github.com/niuulabs/volundr', branch: 'main', path: '/workspace/volundr' },
      ],
      env_vars: { OPENAI_API_KEY: 'secret-ref' },
      env_secret_refs: ['OPENAI_API_KEY'],
      resource_config: { cpu: '4', memory: '8Gi', gpu: 1 },
      mcp_servers: [
        { name: 'filesystem', transport: 'stdio', command: 'uvx mcp-filesystem', tools: ['read'] },
      ],
      workload_config: {
        image: 'ghcr.io/niuulabs/skuld:cuda-12',
        tools: ['python', 'git'],
        ttlSec: 7200,
        idleTimeoutSec: 900,
        clusterAffinity: ['gpu'],
        tolerations: ['nvidia.com/gpu'],
      },
      createdAt: '2026-04-24T12:00:00Z',
      updatedAt: '2026-04-24T12:30:00Z',
    };
    const liveVolundr = {
      kind: 'volundr',
      getSessions: vi.fn().mockResolvedValue([]),
      getSession: vi.fn().mockResolvedValue(null),
      getTemplates: vi.fn().mockResolvedValue([liveTemplate]),
      getTemplate: vi.fn().mockResolvedValue(liveTemplate),
      listArchivedSessions: vi.fn().mockResolvedValue([]),
      deleteSession: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn(() => () => {}),
    };
    volundrMocks.buildVolundrHttpAdapter.mockReturnValue(liveVolundr as any);

    const services = buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
      },
    } as any);

    const templateStore = services['volundr.templates'] as any;
    await expect(templateStore.listTemplates()).resolves.toEqual([
      expect.objectContaining({
        id: 'niuu-platform',
        name: 'niuu-platform',
        description: 'Full niuu monorepo',
        createdAt: '2026-04-24T12:00:00Z',
        updatedAt: '2026-04-24T12:30:00Z',
        spec: expect.objectContaining({
          image: 'ghcr.io/niuulabs/skuld',
          tag: 'cuda-12',
          env: { OPENAI_API_KEY: 'secret-ref' },
          envSecretRefs: ['OPENAI_API_KEY'],
          tools: ['python', 'git'],
          clusterAffinity: ['gpu'],
          tolerations: ['nvidia.com/gpu'],
          resources: {
            cpuRequest: '4',
            cpuLimit: '4',
            memRequestMi: 8192,
            memLimitMi: 8192,
            gpuCount: 1,
          },
          mounts: [
            {
              name: 'repo-1',
              mountPath: '/workspace/volundr',
              source: {
                kind: 'git',
                repo: 'https://github.com/niuulabs/volundr',
                branch: 'main',
              },
              readOnly: false,
            },
          ],
        }),
      }),
    ]);
    await expect(templateStore.getTemplate('niuu-platform')).resolves.toEqual(
      expect.objectContaining({ id: 'niuu-platform' }),
    );
    await expect(templateStore.createTemplate('new-template', {})).rejects.toThrow(
      'Template creation is not yet supported through the live forge template adapter.',
    );
  });

  it('builds a live cluster adapter from Volundr resources and sessions', async () => {
    const liveVolundr = {
      kind: 'volundr',
      getSessions: vi.fn().mockResolvedValue([
        {
          id: 'sess-running',
          name: 'agent-runtime',
          podName: 'forge-pod-1',
          status: 'running',
          lastActive: Date.parse('2026-04-24T12:30:00Z'),
          source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
          model: 'claude-sonnet',
          messageCount: 0,
          tokensUsed: 0,
          activityState: 'active',
        },
        {
          id: 'sess-queued',
          name: 'queued-runtime',
          status: 'provisioning',
          lastActive: 0,
          source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
          model: 'claude-sonnet',
          messageCount: 0,
          tokensUsed: 0,
          activityState: null,
        },
      ]),
      getSession: vi.fn().mockResolvedValue(null),
      getClusterResources: vi.fn().mockResolvedValue({
        resourceTypes: [],
        nodes: [
          {
            name: 'node-a',
            labels: {
              'topology.kubernetes.io/region': 'ca-hamilton-1',
            },
            allocatable: {
              cpu: '8',
              memory: '16Gi',
              'nvidia.com/gpu': '1',
            },
            allocated: {
              cpu: '1500m',
              memory: '8Gi',
              'nvidia.com/gpu': '1',
            },
            available: {},
          },
          {
            name: 'node-b',
            labels: {
              'node-role.kubernetes.io/control-plane': 'true',
            },
            allocatable: {
              cpu: '4',
              memory: '8Gi',
            },
            allocated: {
              cpu: '500m',
              memory: '1Gi',
            },
            available: {},
          },
        ],
      }),
      getTemplates: vi.fn().mockResolvedValue([]),
      getTemplate: vi.fn().mockResolvedValue(null),
      listArchivedSessions: vi.fn().mockResolvedValue([]),
      deleteSession: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn(() => () => {}),
    };
    volundrMocks.buildVolundrHttpAdapter.mockReturnValue(liveVolundr as any);

    const services = buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
      },
    } as any);

    const clusterAdapter = services['volundr.clusters'] as any;
    await expect(clusterAdapter.getClusters()).resolves.toEqual([
      expect.objectContaining({
        id: 'shared',
        name: 'Shared GPU Forge',
        kind: 'gpu',
        region: 'ca-hamilton-1',
        capacity: { cpu: 12, memMi: 24576, gpu: 1 },
        used: { cpu: 2, memMi: 9216, gpu: 1 },
        runningSessions: 1,
        queuedProvisions: 1,
        pods: [
          expect.objectContaining({
            name: 'forge-pod-1',
            status: 'running',
            startedAt: '2026-04-24T12:30:00.000Z',
          }),
          expect.objectContaining({
            name: 'queued-runtime',
            status: 'pending',
            startedAt: '1970-01-01T00:00:00.000Z',
          }),
        ],
        nodes: [
          { id: 'node-a', status: 'ready', role: 'worker' },
          { id: 'node-b', status: 'ready', role: 'control-plane' },
        ],
      }),
    ]);
    expect(services.clusterAdapter).toBe(services['volundr.clusters']);
    await expect(clusterAdapter.getCluster('shared')).resolves.toEqual(
      expect.objectContaining({ id: 'shared' }),
    );
  });

  it('returns no live clusters when Volundr exposes neither nodes nor sessions', async () => {
    const liveVolundr = {
      kind: 'volundr',
      getSessions: vi.fn().mockResolvedValue([]),
      getSession: vi.fn().mockResolvedValue(null),
      getClusterResources: vi.fn().mockResolvedValue({ resourceTypes: [], nodes: [] }),
      getTemplates: vi.fn().mockResolvedValue([]),
      getTemplate: vi.fn().mockResolvedValue(null),
      listArchivedSessions: vi.fn().mockResolvedValue([]),
      deleteSession: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn(() => () => {}),
    };
    volundrMocks.buildVolundrHttpAdapter.mockReturnValue(liveVolundr as any);

    const services = buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
      },
    } as any);

    const clusterAdapter = services['volundr.clusters'] as any;
    await expect(clusterAdapter.getClusters()).resolves.toEqual([]);
    await expect(clusterAdapter.getCluster('shared')).resolves.toBeNull();
  });

  it('normalizes sparse live templates and rejects unsupported live template mutations', async () => {
    const sparseTemplate = {
      name: 'edge-template',
      description: '',
      repos: [
        { url: 'https://github.com/niuulabs/platform', repo: 'platform tools' },
        { branch: 'dev' },
      ],
      envVars: { FEATURE_FLAG: 'on', retries: 3 },
      envSecretRefs: 'not-an-array',
      resourceConfig: {
        cpuRequest: '2',
        memory_request: '512Ki',
        memory_limit: '1Ti',
      },
      workloadConfig: {
        image: 'ghcr.io/niuulabs/skuld',
        cluster_affinity: ['edge'],
        ttlSec: Number.NaN,
        idleTimeoutSec: Number.NaN,
      },
    };
    const remoteTemplate = {
      name: 'remote-template',
      description: 'remote',
      repos: [],
      mcpServers: [
        { name: 'remote', transport: 'sse', url: 'https://example.com/sse', tools: ['sync', 1] },
        { command: 'uvx mcp-shell' },
      ],
    };
    const liveVolundr = {
      kind: 'volundr',
      getSessions: vi.fn().mockResolvedValue([]),
      getSession: vi.fn().mockResolvedValue(null),
      getTemplates: vi.fn().mockResolvedValue([sparseTemplate, remoteTemplate]),
      getTemplate: vi.fn().mockResolvedValue(null),
      listArchivedSessions: vi.fn().mockResolvedValue([]),
      deleteSession: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn(() => () => {}),
    };
    volundrMocks.buildVolundrHttpAdapter.mockReturnValue(liveVolundr as any);

    const services = buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
        mimir: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/mimir' },
      },
    } as any);

    const templateStore = services['volundr.templates'] as any;
    await expect(templateStore.listTemplates()).resolves.toEqual([
      expect.objectContaining({
        id: 'edge-template',
        description: undefined,
        createdAt: '1970-01-01T00:00:00.000Z',
        updatedAt: '1970-01-01T00:00:00.000Z',
        spec: expect.objectContaining({
          image: 'ghcr.io/niuulabs/skuld',
          tag: 'latest',
          env: { FEATURE_FLAG: 'on' },
          envSecretRefs: [],
          mcpServers: [],
          clusterAffinity: ['edge'],
          ttlSec: 3600,
          idleTimeoutSec: 600,
          resources: {
            cpuRequest: '2',
            cpuLimit: '2',
            memRequestMi: 1,
            memLimitMi: 1048576,
            gpuCount: 0,
          },
          mounts: [
            {
              name: 'platform tools',
              mountPath: '/workspace/platform-tools',
              source: {
                kind: 'git',
                repo: 'https://github.com/niuulabs/platform',
                branch: 'main',
              },
              readOnly: false,
            },
          ],
        }),
      }),
      expect.objectContaining({
        id: 'remote-template',
        spec: expect.objectContaining({
          mcpServers: [
            {
              name: 'remote',
              transport: 'sse',
              connectionString: 'https://example.com/sse',
              tools: ['sync'],
            },
            {
              name: 'server-2',
              transport: 'stdio',
              connectionString: 'uvx mcp-shell',
              tools: [],
            },
          ],
        }),
      }),
    ]);
    await expect(templateStore.getTemplate('missing-template')).resolves.toBeNull();
    await expect(templateStore.updateTemplate('edge-template', {})).rejects.toThrow(
      'Template updates are not yet supported through the live forge template adapter.',
    );
    await expect(templateStore.deleteTemplate('edge-template')).rejects.toThrow(
      'Template deletion is not yet supported through the live forge template adapter.',
    );
    expect(services.mimir).toEqual({});
  });

  it('falls back to beta/shared cluster regions and maps failed or finished sessions', async () => {
    const liveVolundr = {
      kind: 'volundr',
      getSessions: vi.fn().mockResolvedValue([
        {
          id: 'sess-error',
          name: 'broken-session',
          status: 'failed',
          lastActive: Date.parse('2026-04-24T12:45:00Z'),
          source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
          model: 'claude-sonnet',
          messageCount: 0,
          tokensUsed: 0,
          activityState: null,
        },
        {
          id: 'sess-stopped',
          name: 'done-session',
          status: 'stopped',
          lastActive: Date.parse('2026-04-24T12:50:00Z'),
          source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
          model: 'claude-sonnet',
          messageCount: 0,
          tokensUsed: 0,
          activityState: null,
        },
      ]),
      getSession: vi.fn().mockResolvedValue(null),
      getClusterResources: vi
        .fn()
        .mockResolvedValueOnce({
          resourceTypes: [],
          nodes: [
            {
              name: 'node-c',
              labels: {
                'failure-domain.beta.kubernetes.io/region': 'ca-toronto',
              },
              allocatable: {},
              allocated: {},
              available: {},
            },
          ],
        })
        .mockRejectedValueOnce(new Error('cluster resources unavailable')),
      getTemplates: vi.fn().mockResolvedValue([]),
      getTemplate: vi.fn().mockResolvedValue(null),
      listArchivedSessions: vi.fn().mockResolvedValue([]),
      deleteSession: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn(() => () => {}),
    };
    volundrMocks.buildVolundrHttpAdapter.mockReturnValue(liveVolundr as any);

    const services = buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
      },
    } as any);

    const clusterAdapter = services['volundr.clusters'] as any;
    await expect(clusterAdapter.getClusters()).resolves.toEqual([
      expect.objectContaining({
        region: 'ca-toronto',
        status: 'healthy',
        pods: [
          expect.objectContaining({ name: 'broken-session', status: 'failed' }),
          expect.objectContaining({ name: 'done-session', status: 'succeeded' }),
        ],
      }),
    ]);
    await expect(clusterAdapter.getClusters()).resolves.toEqual([
      expect.objectContaining({
        region: 'shared',
        status: 'warning',
      }),
    ]);
  });

  it('keeps mock session stores when Volundr is not live', () => {
    const services = buildServices({
      theme: 'ice',
      plugins: {},
      services: {},
    } as any);

    expect(volundrMocks.createMockSessionStore).toHaveBeenCalledTimes(1);
    expect(volundrMocks.createMockClusterAdapter).toHaveBeenCalledTimes(1);
    expect((services['volundr.templates'] as any).kind).toBe('mock-templates');
    expect((services.features as any).kind).toBe('mock-feature-catalog');
    expect((services.identity as any).kind).toBe('mock-identity');
    expect((services['volundr.sessions'] as any).kind).toBe('mock-session-store');
    expect((services.sessionStore as any).kind).toBe('mock-session-store');
    expect((services['volundr.clusters'] as any).kind).toBe('mock-clusters');
    expect((services.clusterAdapter as any).kind).toBe('mock-clusters');
  });

  it('maps lifecycle variants and subscription updates through the live session store', async () => {
    const liveSessions = [
      {
        id: 'sess-created',
        name: 'draft/session',
        source: {
          type: 'local_mount',
          local_path: '/workspace/niuu',
          paths: [{ host_path: '/workspace/niuu', mount_path: '/workspace', read_only: false }],
        },
        status: 'created',
        model: 'claude-sonnet',
        lastActive: 0,
        messageCount: 0,
        tokensUsed: 0,
        ownerId: 'ravn-created',
        activityState: null,
      },
      {
        id: 'sess-starting',
        name: 'booting/session',
        source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'boot' },
        status: 'starting',
        model: 'claude-sonnet',
        lastActive: Date.parse('2026-04-24T10:00:00Z'),
        messageCount: 0,
        tokensUsed: 15,
        tenantId: 'tenant-a',
        activityState: null,
      },
      {
        id: 'sess-idle',
        name: 'idle/session',
        source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'idle' },
        status: 'running',
        model: 'claude-sonnet',
        lastActive: Date.parse('2026-04-24T11:00:00Z'),
        messageCount: 0,
        tokensUsed: 20,
        podName: 'forge-pod-1',
        activityState: 'idle',
      },
      {
        id: 'sess-stopping',
        name: 'stopping/session',
        source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'stop' },
        status: 'stopping',
        model: 'claude-sonnet',
        lastActive: Date.parse('2026-04-24T11:30:00Z'),
        messageCount: 0,
        tokensUsed: 25,
        hostname: 'forge-host',
        activityState: null,
      },
    ];
    const archivedSession = {
      id: 'sess-error',
      name: 'error/session',
      source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'err' },
      status: 'failed',
      model: 'claude-haiku',
      lastActive: Date.parse('2026-04-23T12:30:00Z'),
      messageCount: 0,
      tokensUsed: 0,
      activityState: null,
      archivedAt: new Date('2026-04-23T13:00:00Z'),
    };
    const liveVolundr = {
      kind: 'volundr',
      getSessions: vi.fn().mockResolvedValue(liveSessions),
      getSession: vi.fn().mockResolvedValue(null),
      listArchivedSessions: vi.fn().mockResolvedValue([archivedSession]),
      deleteSession: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn((callback: (sessions: typeof liveSessions) => void) => {
        callback(liveSessions);
        return () => {};
      }),
    };
    volundrMocks.buildVolundrHttpAdapter.mockReturnValue(liveVolundr as any);

    const services = buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
      },
    } as any);
    const sessionStore = services.sessionStore as any;

    await expect(sessionStore.listSessions()).resolves.toEqual([
      expect.objectContaining({
        id: 'sess-created',
        state: 'requested',
        startedAt: '1970-01-01T00:00:00.000Z',
        templateId: '/workspace/niuu',
        clusterId: 'shared',
        ravnId: 'ravn-created',
        preview: '/workspace/niuu',
      }),
      expect.objectContaining({
        id: 'sess-starting',
        state: 'provisioning',
        bootProgress: 0.25,
        clusterId: 'tenant-a',
        ravnId: 'tenant-a',
      }),
      expect.objectContaining({
        id: 'sess-idle',
        state: 'idle',
        clusterId: 'forge-pod-1',
      }),
      expect.objectContaining({
        id: 'sess-stopping',
        state: 'terminating',
        clusterId: 'forge-host',
      }),
      expect.objectContaining({
        id: 'sess-error',
        state: 'failed',
        terminatedAt: '2026-04-23T13:00:00.000Z',
      }),
    ]);
    await expect(sessionStore.listSessions({ clusterId: 'forge-pod-1' })).resolves.toEqual([
      expect.objectContaining({ id: 'sess-idle' }),
    ]);
    await expect(sessionStore.listSessions({ ravnId: 'tenant-a' })).resolves.toEqual([
      expect.objectContaining({ id: 'sess-starting' }),
    ]);
    await expect(sessionStore.createSession({})).rejects.toThrow(/not yet supported/);
    await expect(sessionStore.updateSession('sess-idle', {})).rejects.toThrow(/not yet supported/);

    const callback = vi.fn();
    const unsubscribe = sessionStore.subscribe(callback);
    await Promise.resolve();
    await Promise.resolve();
    expect(callback).toHaveBeenCalled();
    expect(callback.mock.calls.at(-1)?.[0]).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 'sess-idle', state: 'idle' }),
        expect.objectContaining({ id: 'sess-error', state: 'failed' }),
      ]),
    );
    unsubscribe();
  });

  it('builds live stream and observatory adapters when those backends are configured', () => {
    buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        'volundr.pty': { mode: 'ws', wsUrl: 'ws://localhost:8080/api/v1/volundr/pty/{sessionId}' },
        'volundr.metrics': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/volundr/metrics',
        },
        'observatory.registry': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/observatory/registry',
        },
        'observatory.topology': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/observatory/topology',
        },
        'observatory.events': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/observatory/events',
        },
      },
    } as any);

    expect(volundrMocks.buildVolundrPtyWsAdapter).toHaveBeenCalledWith({
      urlTemplate: 'ws://localhost:8080/api/v1/volundr/pty/{sessionId}',
    });
    expect(volundrMocks.buildVolundrMetricsSseAdapter).toHaveBeenCalledWith({
      urlTemplate: 'http://localhost:8080/api/v1/volundr/metrics',
    });
    expect(queryMocks.createApiClient).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/observatory',
    );
    expect(observatoryMocks.buildObservatoryRegistryHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/observatory',
    });
    expect(observatoryMocks.buildObservatoryTopologySseStream).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/observatory/topology',
    );
    expect(observatoryMocks.buildObservatoryEventsSseStream).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/observatory/events',
    );
  });

  it('derives the bundled host pty websocket path from the live forge base when no explicit pty config exists', () => {
    buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
      },
    } as any);

    expect(volundrMocks.buildVolundrPtyWsAdapter).toHaveBeenCalledWith({
      urlTemplate: 'ws://localhost:8080/s/{sessionId}/session',
    });
  });

  it('prefers canonical Forge stream configs over the legacy Volundr stream keys', () => {
    buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        'forge.pty': { mode: 'ws', wsUrl: 'ws://localhost:8080/api/v1/forge/pty/{sessionId}' },
        'forge.metrics': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/forge/metrics',
        },
        'volundr.pty': { mode: 'ws', wsUrl: 'ws://localhost:8080/api/v1/volundr/pty/{sessionId}' },
        'volundr.metrics': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/volundr/metrics',
        },
      },
    } as any);

    expect(volundrMocks.buildVolundrPtyWsAdapter).toHaveBeenCalledWith({
      urlTemplate: 'ws://localhost:8080/api/v1/forge/pty/{sessionId}',
    });
    expect(volundrMocks.buildVolundrMetricsSseAdapter).toHaveBeenCalledWith({
      urlTemplate: 'http://localhost:8080/api/v1/forge/metrics',
    });
  });

  it('lets a grouped observatory base drive all observatory adapters by default', () => {
    buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        observatory: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/observatory' },
      },
    } as any);

    expect(queryMocks.createApiClient).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/observatory',
    );
    expect(observatoryMocks.buildObservatoryRegistryHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/observatory',
    });
    expect(observatoryMocks.buildObservatoryTopologySseStream).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/observatory/topology',
    );
    expect(observatoryMocks.buildObservatoryEventsSseStream).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/observatory/events',
    );
  });

  it('prefers explicit observatory surface overrides over the grouped observatory base', () => {
    buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        observatory: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/observatory' },
        'observatory.events': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/observatory/events-stream',
        },
      },
    } as any);

    expect(queryMocks.createApiClient).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/observatory',
    );
    expect(observatoryMocks.buildObservatoryEventsSseStream).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/observatory/events-stream',
    );
  });

  it('normalizes an explicit observatory registry override back to the service root', () => {
    buildServices({
      theme: 'ice',
      plugins: {},
      services: {
        'observatory.registry': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/observatory/registry',
        },
      },
    } as any);

    expect(queryMocks.createApiClient).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/observatory',
    );
  });
});
