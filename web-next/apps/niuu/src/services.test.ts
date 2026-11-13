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

const bifrostMocks = vi.hoisted(() => ({
  createMockBifrostService: vi.fn(() => ({ kind: 'mock-bifrost' })),
  buildBifrostHttpAdapter: vi.fn((client) => ({ kind: 'bifrost', client })),
}));

const tingMocks = vi.hoisted(() => ({
  RunWorkflowGraph: () => null,
  buildRunGateService: vi.fn(() => ({ kind: 'run-gates' })),
  createMockTingService: vi.fn(() => ({ kind: 'mock-ting' })),
  createMockDispatcherService: vi.fn(() => ({ kind: 'mock-dispatcher' })),
  createMockTingSessionService: vi.fn(() => ({ kind: 'mock-sessions' })),
  createMockTrackerService: vi.fn(() => ({ kind: 'mock-tracker' })),
  createMockWorkService: vi.fn(() => ({ kind: 'mock-work' })),
  createMockRunLogService: vi.fn(() => ({ kind: 'mock-run-log' })),
  createMockWorkflowService: vi.fn(() => ({ kind: 'mock-workflows' })),
  createMockResearchService: vi.fn(() => ({ kind: 'mock-research' })),
  createMockSpecsService: vi.fn(() => ({ kind: 'mock-specs' })),
  createMockDispatchBus: vi.fn(() => ({ kind: 'mock-dispatch' })),
  createMockTingSettingsService: vi.fn(() => ({ kind: 'mock-settings' })),
  createMockAuditLogService: vi.fn(() => ({ kind: 'mock-audit' })),
  buildTingHttpAdapter: vi.fn((client) => ({ kind: 'ting', client })),
  buildDispatcherHttpAdapter: vi.fn((client) => ({ kind: 'dispatcher', client })),
  buildTingSessionHttpAdapter: vi.fn((client) => ({ kind: 'sessions', client })),
  buildTrackerHttpAdapter: vi.fn((client) => ({ kind: 'tracker', client })),
  buildWorkflowHttpAdapter: vi.fn((client) => ({ kind: 'workflows', client })),
  buildWorkHttpAdapter: vi.fn((client) => ({ kind: 'work', client })),
  buildRunLogHttpAdapter: vi.fn((client) => ({ kind: 'run-log', client })),
  buildWorkflowExecutionHttpAdapter: vi.fn((client) => ({ kind: 'workflow-executions', client })),
  buildDeliveryExecutionHttpAdapter: vi.fn((client) => ({ kind: 'delivery-executions', client })),
  buildResearchHttpAdapter: vi.fn((client) => ({ kind: 'research', client })),
  buildSpecsHttpAdapter: vi.fn((client) => ({ kind: 'specs', client })),
  buildDispatchBusHttpAdapter: vi.fn((client) => ({ kind: 'dispatch', client })),
  buildTingSettingsHttpAdapter: vi.fn((client) => ({ kind: 'settings', client })),
  buildTingAuditLogHttpAdapter: vi.fn((client) => ({ kind: 'audit', client })),
  buildMemoryImportsHttpAdapter: vi.fn((client) => ({ kind: 'memory-imports', client })),
  createMockMemoryImportsService: vi.fn(() => ({ kind: 'mock-memory-imports' })),
  MEMORY_IMPORTS_SERVICE_KEY: 'ting.memoryImports',
}));

const ravnMocks = vi.hoisted(() => ({
  createMockPersonaStore: vi.fn(() => ({})),
  buildRavnPersonaAdapter: vi.fn(() => ({})),
}));

const observatoryMocks = vi.hoisted(() => ({
  createMockAgentDirectory: vi.fn(() => ({ kind: 'mock-observatory-agents' })),
  createMockRegistryRepository: vi.fn(() => ({})),
  createMockTopologyStream: vi.fn(() => ({})),
  createMockEventStream: vi.fn(() => ({})),
  buildObservatoryRegistryHttpAdapter: vi.fn(() => ({})),
  buildObservatoryTopologyAggregateAdapter: vi.fn(() => ({})),
  buildObservatoryTopologySseStream: vi.fn(() => ({})),
  buildObservatoryEventsSseStream: vi.fn(() => ({})),
  buildObservatoryAgentDirectoryHttpAdapter: vi.fn((client) => ({
    kind: 'observatory-agents',
    client,
  })),
}));

const realmsMocks = vi.hoisted(() => ({
  REALM_GOVERNANCE_SERVICE_KEY: 'realms.governance',
  createMockRealmGovernanceService: vi.fn(() => ({ kind: 'mock-realms' })),
  buildRealmGovernanceHttpAdapter: vi.fn((realmsClient, realmsStreamBase) => ({
    kind: 'realms',
    realmsClient,
    realmsStreamBase,
  })),
}));

const volundrMocks = vi.hoisted(() => ({
  RUN_WORKFLOW_VIEW_SERVICE: 'volundr.runWorkflowView',
  RUN_GATE_SERVICE: 'volundr.runGates',
  createMockVolundrService: vi.fn(() => ({ kind: 'mock-volundr' })),
  createMockClusterAdapter: vi.fn(() => ({ kind: 'mock-clusters' })),
  createMockSessionStore: vi.fn(() => ({ kind: 'mock-session-store' })),
  buildVolundrHttpAdapter: vi.fn((client) => ({
    kind: 'volundr',
    client,
    getSessions: vi.fn().mockResolvedValue([]),
    getSession: vi.fn().mockResolvedValue(null),
    getClusterResources: vi.fn().mockResolvedValue({ resourceTypes: [], nodes: [] }),
    getLaunchSpecs: vi.fn().mockResolvedValue([]),
    getLaunchSpec: vi.fn().mockResolvedValue(null),
    listArchivedSessions: vi.fn().mockResolvedValue([]),
    archiveStoppedSessions: vi.fn().mockResolvedValue([]),
    deleteSession: vi.fn().mockResolvedValue(undefined),
    subscribe: vi.fn(() => () => {}),
  })),
  createMockPtyStream: vi.fn(() => ({})),
  createMockMetricsStream: vi.fn(() => ({})),
  createMockFileSystemPort: vi.fn(() => ({})),
  buildVolundrFileSystemHttpAdapter: vi.fn((options) => ({ kind: 'filesystem', options })),
  buildVolundrPtyWsAdapter: vi.fn(() => ({})),
  buildVolundrMetricsSseAdapter: vi.fn(() => ({})),
  buildNotificationFeedHttpAdapter: vi.fn((client, options) => ({
    kind: 'notifications',
    client,
    options,
  })),
  createMockNotificationFeed: vi.fn(() => ({ kind: 'mock-notifications' })),
  forgeApiBasePath: vi.fn((basePath: string) => basePath),
}));

vi.mock('@niuulabs/query', () => ({
  createApiClient: queryMocks.createApiClient,
}));
vi.mock('@niuulabs/plugin-sdk', () => pluginSdkMocks);

vi.mock('@niuulabs/plugin-bifrost', () => bifrostMocks);
vi.mock('@niuulabs/plugin-ting', () => tingMocks);
vi.mock('@niuulabs/plugin-ravn', () => ravnMocks);
vi.mock('@niuulabs/plugin-mimir', () => ({
  createMimirMockAdapter: vi.fn(() => ({})),
  buildMimirHttpAdapter: vi.fn(() => ({})),
  buildMemoryGatewayHttpAdapter: vi.fn((client) => ({ kind: 'memory-gateway', client })),
  createMemoryGatewayMockAdapter: vi.fn(() => ({ kind: 'mock-memory-gateway' })),
  MEMORY_GATEWAY_SERVICE_KEY: 'mimir.memory',
}));
vi.mock('@niuulabs/plugin-setup', () => ({
  createMockSetupService: vi.fn(() => ({})),
  buildSetupHttpAdapter: vi.fn(() => ({})),
}));
vi.mock('@niuulabs/plugin-observatory', () => observatoryMocks);
vi.mock('@niuulabs/plugin-realms', () => realmsMocks);
vi.mock('@niuulabs/plugin-volundr', () => volundrMocks);

import {
  buildServices,
  ServiceUnavailableError,
  isUnavailableService,
  UnsupportedSessionStoreOperationError,
  buildServiceBackendStatus,
  resolveCanonicalServiceBase,
  resolveForgeServiceBase,
  buildSharedFeatureCatalogService,
  buildSharedIdentityService,
  resolveSharedApiBase,
  resolveNiuuRegistryBase,
  toSharedApiBase,
  toHostBase,
  toHostPtyWsUrl,
  resolveSettingsServiceBase,
} from './services';

describe('toSharedApiBase', () => {
  it('strips a trailing Ting service suffix', () => {
    expect(toSharedApiBase('http://localhost:8080/api/v1/ting')).toBe(
      'http://localhost:8080/api/v1',
    );
  });

  it('strips a trailing Volundr service suffix', () => {
    expect(toSharedApiBase('http://localhost:8080/api/v1/forge')).toBe(
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
    expect(toHostBase('http://localhost:8080/api/v1/forge')).toBe('http://localhost:8080');
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
  it('prefers the Ting shared base when Ting is live', () => {
    expect(
      resolveSharedApiBase({
        services: {
          ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
          volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
        },
      } as any),
    ).toBe('http://localhost:8080/api/v1');
  });

  it('falls back to the canonical Forge shared base when Ting is not live', () => {
    expect(
      resolveSharedApiBase({
        services: {
          forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
        },
      } as any),
    ).toBe('http://localhost:8080/api/v1');
  });

  it('falls back to the Volundr catalog shared base when Ting is not live', () => {
    expect(
      resolveSharedApiBase({
        services: {
          volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
        },
      } as any),
    ).toBe('http://localhost:8080/api/v1');
  });

  it('returns null when neither Ting nor Volundr is live', () => {
    expect(
      resolveSharedApiBase({
        services: {
          ting: { mode: 'mock' },
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
            ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
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

describe('resolveNiuuRegistryBase', () => {
  it('prefers an explicit niuu registry base when configured', () => {
    expect(
      resolveNiuuRegistryBase({
        services: {
          niuu: { mode: 'http', baseUrl: 'https://niuu.yggdrasil.niuu.world/api/v1/niuu' },
          forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
        },
      } as any),
    ).toBe('https://niuu.yggdrasil.niuu.world/api/v1/niuu');
  });

  it('falls back to the local shared niuu route when no explicit base is configured', () => {
    expect(
      resolveNiuuRegistryBase({
        services: {
          forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
        },
      } as any),
    ).toBe('http://localhost:8080/api/v1/niuu');
  });
});

describe('resolveSettingsServiceBase', () => {
  it('resolves the host runtime settings from the setup API', () => {
    expect(
      resolveSettingsServiceBase(
        {
          services: {
            niuu: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/niuu' },
          },
        } as any,
        'runtime',
      ),
    ).toBe('http://localhost:8080/api/v1/niuu/setup');
    expect(
      resolveSettingsServiceBase(
        {
          services: {
            setup: { mode: 'http', baseUrl: 'http://localhost:9090/api/v1/niuu/setup' },
          },
        } as any,
        'runtime',
      ),
    ).toBe('http://localhost:9090/api/v1/niuu/setup');
  });

  it('resolves volundr settings from the forge base, where the settings router lives', () => {
    expect(
      resolveSettingsServiceBase(
        {
          services: {
            forge: { mode: 'http', baseUrl: '/api/v1/forge' },
            volundr: { mode: 'http', baseUrl: '/api/v1/volundr' },
          },
        } as any,
        'volundr',
      ),
    ).toBe('/api/v1/forge');
    expect(
      resolveSettingsServiceBase(
        {
          services: {
            volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
          },
        } as any,
        'volundr',
      ),
    ).toBe('http://localhost:8080/api/v1/forge');
  });

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

  it('derives identity settings from the shared API root when needed', () => {
    expect(
      resolveSettingsServiceBase(
        {
          services: {
            identity: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1' },
          },
        } as any,
        'identity',
      ),
    ).toBe('http://localhost:8080/api/v1/identity');
  });

  it('resolves ting settings from the normalized ting base', () => {
    expect(
      resolveSettingsServiceBase(
        {
          services: {
            'ting.settings': {
              mode: 'http',
              baseUrl: 'http://localhost:8080/api/v1/ting/settings',
            },
          },
        } as any,
        'ting',
      ),
    ).toBe('http://localhost:8080/api/v1/ting');
  });

  it('resolves bifrost settings from the grouped bifrost base', () => {
    expect(
      resolveSettingsServiceBase(
        {
          services: {
            bifrost: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/bifrost' },
          },
        } as any,
        'bifrost',
      ),
    ).toBe('http://localhost:8080/api/v1/bifrost');
  });

  it('resolves shared credentials settings from the grouped credentials base', () => {
    expect(
      resolveSettingsServiceBase(
        {
          services: {
            credentials: {
              mode: 'http',
              baseUrl: 'http://localhost:8080/api/v1/credentials',
            },
          },
        } as any,
        'credentials',
      ),
    ).toBe('http://localhost:8080/api/v1/credentials');
  });

  it('resolves shared integrations settings from the grouped integrations base', () => {
    expect(
      resolveSettingsServiceBase(
        {
          services: {
            integrations: {
              mode: 'http',
              baseUrl: 'http://localhost:8080/api/v1/integrations',
            },
          },
        } as any,
        'integrations',
      ),
    ).toBe('http://localhost:8080/api/v1/integrations');
  });
});

describe('resolveForgeServiceBase', () => {
  it('uses the explicit forge domain base', () => {
    expect(
      resolveForgeServiceBase({
        services: {
          forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
          volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
        },
      } as any),
    ).toBe('http://localhost:8080/api/v1/forge');
  });

  it('does not treat the volundr catalog base as forge runtime', () => {
    expect(
      resolveForgeServiceBase({
        services: {
          volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
        },
      } as any),
    ).toBeNull();
  });

  it('returns null when forge is not live', () => {
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
  it('sends project writes to the Forge that lists the projects', async () => {
    const forge = {
      createProject: vi.fn(async () => ({ id: 'project-1' })),
      updateProject: vi.fn(async () => ({ id: 'project-1' })),
    };
    volundrMocks.createMockVolundrService.mockImplementationOnce(() => forge as never);
    const volundr = buildServices({ demoMode: true, services: {} } as any).volundr as any;

    await volundr.createProject('Home lab', { instanceId: 'ymir' });
    await volundr.updateProject({ id: 'project-1' }, { brief: 'The rack in the shed' });

    expect(forge.createProject).toHaveBeenCalledWith('Home lab', { instanceId: 'ymir' });
    expect(forge.updateProject).toHaveBeenCalledWith(
      { id: 'project-1' },
      { brief: 'The rack in the shed' },
    );
  });

  it('sends compute host reads and changes to the Forge that owns the hosts', async () => {
    const forge = {
      getComputeHosts: vi.fn(async () => null),
      getComputeHostTagSets: vi.fn(async () => [['gpu']]),
      createPairingCode: vi.fn(async () => ({ id: 'c-1' })),
      revokePairingCode: vi.fn(async () => undefined),
      updateComputeHost: vi.fn(async () => ({ id: 'h-1' })),
      drainComputeHost: vi.fn(async () => ({ id: 'h-1' })),
      resumeComputeHost: vi.fn(async () => ({ id: 'h-1' })),
      reprepareComputeHost: vi.fn(async () => ({ id: 'h-1' })),
      checkComputeHost: vi.fn(async () => ({ id: 'h-1' })),
      removeComputeHost: vi.fn(async () => ({ removed: true, hostCleanupCommand: null })),
    };
    volundrMocks.createMockVolundrService.mockImplementationOnce(() => forge as never);
    const volundr = buildServices({ demoMode: true, services: {} } as any).volundr as any;
    const request = { execution: 'host@1', connector: 'direct-ssh', tags: [], slots: null };

    await volundr.getComputeHosts('ymir');
    await volundr.getComputeHostTagSets('ymir');
    await volundr.createPairingCode('ymir', request);
    await volundr.revokePairingCode('ymir', 'c-1');
    await volundr.updateComputeHost('ymir', 'h-1', { tags: ['gpu'] });
    await volundr.drainComputeHost('ymir', 'h-1', true);
    await volundr.resumeComputeHost('ymir', 'h-1');
    await volundr.reprepareComputeHost('ymir', 'h-1');
    await volundr.checkComputeHost('ymir', 'h-1');
    await volundr.removeComputeHost('ymir', 'h-1');

    expect(forge.getComputeHosts).toHaveBeenCalledWith('ymir');
    expect(forge.getComputeHostTagSets).toHaveBeenCalledWith('ymir');
    expect(forge.createPairingCode).toHaveBeenCalledWith('ymir', request);
    expect(forge.revokePairingCode).toHaveBeenCalledWith('ymir', 'c-1');
    expect(forge.updateComputeHost).toHaveBeenCalledWith('ymir', 'h-1', { tags: ['gpu'] });
    expect(forge.drainComputeHost).toHaveBeenCalledWith('ymir', 'h-1', true);
    expect(forge.resumeComputeHost).toHaveBeenCalledWith('ymir', 'h-1');
    expect(forge.reprepareComputeHost).toHaveBeenCalledWith('ymir', 'h-1');
    expect(forge.checkComputeHost).toHaveBeenCalledWith('ymir', 'h-1');
    expect(forge.removeComputeHost).toHaveBeenCalledWith('ymir', 'h-1');
  });

  it('serves the observatory agent directory from the mock in demo mode', () => {
    const services = buildServices({ demoMode: true, services: {} } as any);

    expect(observatoryMocks.createMockAgentDirectory).toHaveBeenCalled();
    expect(services['observatory.agents']).toEqual({ kind: 'mock-observatory-agents' });
  });

  it('locates session history on the Forge API rather than the session gateway host', () => {
    const services = buildServices({
      demoMode: true,
      services: { forge: { mode: 'http', baseUrl: 'https://app.test/api/v1/forge' } },
    } as any);
    const locator = services['forge.history'] as {
      historyEndpoint(url: string | null): string | null;
    };

    expect(locator.historyEndpoint('wss://sessions.cluster.test/s/abc/session')).toBe(
      'https://app.test/api/v1/forge/sessions/abc/conversation',
    );
  });

  it('registers no history locator without a live Forge base', () => {
    const services = buildServices({ demoMode: true, services: {} } as any);

    expect(services['forge.history']).toBeUndefined();
  });

  it('leaves the agent directory unavailable outside demo mode', () => {
    const services = buildServices({ demoMode: false, services: {} } as any);

    expect(observatoryMocks.createMockAgentDirectory).not.toHaveBeenCalled();
    expect(isUnavailableService(services['observatory.agents'])).toBe(true);
  });

  it('serves work from the mock only when demo mode is explicit', () => {
    const demo = buildServices({ demoMode: true, services: {} } as any);
    expect(demo['ting.work']).toEqual({ kind: 'mock-work' });

    tingMocks.createMockWorkService.mockClear();
    const live = buildServices({ demoMode: false, services: {} } as any);
    expect(tingMocks.createMockWorkService).not.toHaveBeenCalled();
    expect(isUnavailableService(live['ting.work'])).toBe(true);
  });

  it('wires the memory gateway and memory imports to Mímir and Ting, mocks only in demo', () => {
    const live = buildServices({
      demoMode: false,
      services: {
        mimir: { mode: 'http', baseUrl: '/api/v1/mimir' },
        ting: { mode: 'http', baseUrl: '/api/v1/ting' },
      },
    } as any);
    expect(live['mimir.memory']).toMatchObject({ kind: 'memory-gateway' });
    expect(live['ting.memoryImports']).toMatchObject({ kind: 'memory-imports' });
    const demo = buildServices({ demoMode: true, services: {} } as any);
    expect(demo['mimir.memory']).toEqual({ kind: 'mock-memory-gateway' });
    expect(demo['ting.memoryImports']).toEqual({ kind: 'mock-memory-imports' });
    const off = buildServices({ demoMode: false, services: {} } as any);
    expect(isUnavailableService(off['mimir.memory'])).toBe(true);
    expect(isUnavailableService(off['ting.memoryImports'])).toBe(true);
  });

  it("wires Ting's run workflow view into Forge only where Ting can read the run", () => {
    const demo = buildServices({ demoMode: true, services: {} } as any);
    expect(demo['volundr.runWorkflowView']).toBe(tingMocks.RunWorkflowGraph);
    expect(demo['volundr.runGates']).toEqual({ kind: 'run-gates' });
    expect(tingMocks.buildRunGateService).toHaveBeenCalledWith(demo['ting.work']);

    const live = buildServices({ demoMode: false, services: {} } as any);
    expect(live['volundr.runWorkflowView']).toBeUndefined();
    expect(live['volundr.runGates']).toBeUndefined();
  });

  it('reports work as a demo adapter in demo mode', () => {
    const status = buildServiceBackendStatus({ demoMode: true, services: {} } as any);

    expect(status['ting.work']?.mode).toBe('demo');
  });

  it('builds separate forge runtime and volundr catalog adapters', () => {
    buildServices({
      demoMode: true,
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
        volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
      },
    } as any);

    expect(volundrMocks.buildVolundrHttpAdapter).toHaveBeenCalledWith(
      expect.objectContaining({
        basePath: 'http://localhost:8080/api/v1/forge',
      }),
      undefined,
      expect.objectContaining({
        niuuBasePath: 'http://localhost:8080/api/v1/niuu',
      }),
    );
    expect(volundrMocks.buildVolundrHttpAdapter).toHaveBeenCalledWith(
      expect.objectContaining({
        basePath: 'http://localhost:8080/api/v1/volundr',
      }),
      undefined,
      expect.objectContaining({
        niuuBasePath: 'http://localhost:8080/api/v1/niuu',
      }),
    );
  });

  it('passes the configured Forge scope to the Forge runtime adapter only', () => {
    buildServices({
      demoMode: true,
      services: {
        forge: {
          mode: 'http',
          baseUrl: 'https://niuu.noatun.example/api/v1/forge',
          scope: 'node',
        },
        volundr: { mode: 'http', baseUrl: 'https://niuu.noatun.example/api/v1/volundr' },
      },
    } as any);

    expect(volundrMocks.buildVolundrHttpAdapter).toHaveBeenCalledWith(
      expect.objectContaining({ basePath: 'https://niuu.noatun.example/api/v1/forge' }),
      undefined,
      expect.objectContaining({ forgeScope: 'node' }),
    );
    expect(volundrMocks.buildVolundrHttpAdapter).toHaveBeenCalledWith(
      expect.objectContaining({ basePath: 'https://niuu.noatun.example/api/v1/volundr' }),
      undefined,
      expect.not.objectContaining({ forgeScope: 'node' }),
    );
  });

  it('normalizes explicit Ting sub-service bases back to /api/v1/ting', () => {
    buildServices({
      demoMode: true,
      services: {
        ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
        'ting.dispatcher': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/dispatcher',
        },
        'ting.sessions': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/sessions',
        },
        'ting.dispatch': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/dispatch',
        },
        'ting.settings': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/settings',
        },
        'ting.research': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/research',
        },
        'ting.specs': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/specs',
        },
      },
    } as any);

    expect(tingMocks.buildDispatcherHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
    expect(tingMocks.buildTingSessionHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
    expect(tingMocks.buildDispatchBusHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
    expect(tingMocks.buildTingSettingsHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
    expect(tingMocks.buildResearchHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
    expect(tingMocks.buildSpecsHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
  });
});

describe('shared domain helpers', () => {
  it('builds shared feature catalog and identity services from the canonical shared base', () => {
    const config = {
      services: {
        ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
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
        ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
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

  it('uses mock shared services only in explicit demo mode', () => {
    const config = {
      demoMode: true,
      services: {
        ting: { mode: 'mock' },
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
      },
    } as any);

    expect(status.forge).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1/forge',
      source: 'forge',
    });
    expect(status).not.toHaveProperty('forge.metrics');
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
    expect(status['observatory.agents']).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1/niuu/observatory',
      source: 'niuu',
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

  it('resolves root-relative forge pty websocket URLs against the browser host', () => {
    const status = buildServiceBackendStatus({
      services: {
        'forge.pty': { mode: 'ws', wsUrl: '/s/{sessionId}/session' },
      },
    } as any);

    expect(status['forge.pty']).toEqual({
      mode: 'live',
      transport: 'ws',
      target: 'ws://localhost:3000/s/{sessionId}/session',
      source: 'forge.pty',
    });
  });

  it('reports unavailable workflow and filesystem surfaces explicitly', () => {
    const status = buildServiceBackendStatus({ demoMode: false, services: {} } as any);

    expect(status['ting.workflows']).toEqual({
      mode: 'unavailable',
      transport: 'none',
      target: null,
      source: 'configuration',
    });
    expect(status.filesystem).toEqual({
      mode: 'unavailable',
      transport: 'none',
      target: null,
      source: 'configuration',
      note: 'No live filesystem API is wired yet.',
    });
  });

  it('reports explicit demo adapters separately from unavailable services', () => {
    const status = buildServiceBackendStatus({ demoMode: true, services: {} } as any);

    expect(status['ting.workflows']).toEqual({
      mode: 'demo',
      transport: 'mock',
      target: null,
      source: 'demo',
      note: 'Explicit demo adapter; no live backend is connected.',
    });
  });

  it('derives a live filesystem backend from the Forge facade route', () => {
    const status = buildServiceBackendStatus({
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
      },
    } as any);

    expect(status.filesystem).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1/forge',
      source: 'forge',
    });
  });

  it('normalizes explicit ravn persona bases back to the shared /api/v1 root', () => {
    const status = buildServiceBackendStatus({
      services: {
        'ravn.personas': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn/personas' },
      },
    } as any);

    expect(status['ravn.personas']).toEqual({
      mode: 'live',
      transport: 'http',
      target: 'http://localhost:8080/api/v1',
      source: 'ravn.personas',
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
        ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
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
  it('builds typed unavailable services without aborting optional composition', () => {
    const services = buildServices({
      demoMode: false,
      theme: 'ice',
      plugins: {},
      services: {},
    } as any);

    const personas = services['ravn.personas'];
    expect(isUnavailableService(personas)).toBe(true);
    expect(() => (personas as any).listPersonas()).toThrow(ServiceUnavailableError);
    expect(() => (personas as any).listPersonas()).toThrow('ravn.personas');
  });

  it('builds Ting tracker and audit services against the shared api base', () => {
    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
      },
    } as any);

    expect(tingMocks.buildTingHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
    expect(tingMocks.buildTrackerHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(tingMocks.buildTingAuditLogHttpAdapter).toHaveBeenCalledWith({
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
    expect((services['ting.tracker'] as any).kind).toBe('tracker');
    expect((services['ting.audit'] as any).kind).toBe('audit');
  });

  it('prefers explicit tracker and audit domain configs over the derived shared base', () => {
    buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        tracker: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/tracker' },
        audit: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/audit' },
        ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
      },
    } as any);

    expect(tingMocks.buildTrackerHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/tracker',
    });
    expect(tingMocks.buildTingAuditLogHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/audit',
    });
  });

  it('normalizes explicit Ting subdomain configs back to the shared Ting base', () => {
    buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
        'ting.dispatcher': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/dispatcher',
        },
        'ting.sessions': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/sessions',
        },
        'ting.dispatch': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/dispatch',
        },
        'ting.settings': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/settings',
        },
        'ting.workflows': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/workflows',
        },
        'ting.work': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/work',
        },
        'ting.research': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/research',
        },
        'ting.specs': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/ting/specs',
        },
      },
    } as any);

    expect(tingMocks.buildDispatcherHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
    expect(tingMocks.buildTingSessionHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
    expect(tingMocks.buildDispatchBusHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
    expect(tingMocks.buildTingSettingsHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
    expect(tingMocks.buildWorkflowHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
    expect(tingMocks.buildWorkHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
    expect(tingMocks.buildResearchHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
    expect(tingMocks.buildSpecsHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/ting',
    });
  });

  it('falls back to the Volundr catalog host when Ting is not live', () => {
    buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        volundr: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/volundr' },
      },
    } as any);

    expect(tingMocks.buildTrackerHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(tingMocks.buildTingAuditLogHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
  });

  it('falls back to canonical shared routes when only the Forge base is live', () => {
    buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
      },
    } as any);

    expect(tingMocks.buildTrackerHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(tingMocks.buildTingAuditLogHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(pluginSdkMocks.buildFeatureCatalogAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(pluginSdkMocks.buildIdentityAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
    expect(volundrMocks.buildVolundrFileSystemHttpAdapter).toHaveBeenCalledWith({
      baseUrl: 'http://localhost:8080/api/v1/forge',
    });
  });

  it('builds niuu.repos against the shared repo catalog route', () => {
    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
      },
    } as any);

    expect(queryMocks.createApiClient).toHaveBeenCalledWith('http://localhost:8080/api/v1/niuu');
    expect(services['niuu.repos']).toBeDefined();
  });

  it('routes runtime reads through the Forge facade', async () => {
    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
        ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
      },
    } as any);

    const forgeVolundr = volundrMocks.buildVolundrHttpAdapter.mock.results[0]?.value;
    const facadeSession = {
      id: 'session-facade',
      name: 'facade tracked launch',
      status: 'running',
    };

    forgeVolundr.getSessions.mockResolvedValue([facadeSession]);
    forgeVolundr.getActiveSessions = vi.fn().mockResolvedValue([facadeSession]);
    forgeVolundr.getSession.mockResolvedValue(facadeSession);
    forgeVolundr.listArchivedSessions.mockResolvedValue([]);

    await expect((services.volundr as any).getSessions()).resolves.toEqual([facadeSession]);
    await expect((services.volundr as any).getActiveSessions()).resolves.toEqual([facadeSession]);
    await expect((services.volundr as any).getSession('session-facade')).resolves.toEqual(
      facadeSession,
    );
  });

  it('reads and changes session read state on the Forge host and carries it into the list', async () => {
    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
        ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
      },
    } as any);
    const forgeVolundr = volundrMocks.buildVolundrHttpAdapter.mock.results[0]?.value;
    const readState = {
      isUnread: true,
      latestOutputSeq: 14,
      readThroughSeq: 9,
      revision: 3,
    };
    forgeVolundr.getSessionReadState = vi.fn().mockResolvedValue(readState);
    forgeVolundr.changeSessionReadState = vi.fn().mockResolvedValue({ ...readState, revision: 4 });
    const volundr = services.volundr as any;

    await expect(volundr.getSessionReadState('s1', { instanceId: 'thor' })).resolves.toBe(
      readState,
    );
    expect(forgeVolundr.getSessionReadState).toHaveBeenCalledWith('s1', { instanceId: 'thor' });
    const change = { state: 'read', expectedRevision: 3, throughSeq: 14 };
    await expect(volundr.changeSessionReadState('s1', change)).resolves.toMatchObject({
      revision: 4,
    });
    expect(forgeVolundr.changeSessionReadState).toHaveBeenCalledWith('s1', change, undefined);

    forgeVolundr.getSession.mockResolvedValue({
      id: 's1',
      name: 'alpha',
      status: 'running',
      source: { type: 'git', repo: 'r', branch: 'main' },
      readState,
      shortDescription: 'Tests next',
      workedSeconds24h: 120,
    });
    await expect((services.sessionStore as any).getSession('s1')).resolves.toMatchObject({
      readState,
      shortDescription: 'Tests next',
      workedSeconds24h: 120,
    });
  });

  it("carries a shared session's room and owner into the list without inventing a workspace", async () => {
    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
        ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
      },
    } as any);
    const forgeVolundr = volundrMocks.buildVolundrHttpAdapter.mock.results[0]?.value;
    const room = { role: 'viewer', participantStatus: 'active', expiresAt: null };
    forgeVolundr.getSession.mockResolvedValue({
      id: 'shared-1',
      name: 'alice-feature',
      status: 'running',
      model: 'm',
      source: null,
      room,
      ownerId: 'alice',
      ownerName: 'Alice Smith',
      coordination: { projectId: 'p1', role: 'worker' },
      lastActive: 0,
    });

    const session = await (services.sessionStore as any).getSession('shared-1');

    expect(session).toMatchObject({
      id: 'shared-1',
      room,
      ownerId: 'alice',
      ownerName: 'Alice Smith',
      coordination: { projectId: 'p1', role: 'worker' },
    });
    expect(session.source).toBeUndefined();
    expect(session.templateId).toBeUndefined();
    expect(session.preview).toBeUndefined();
  });

  it('prefers an explicit filesystem base over the derived Volundr route', () => {
    const services = buildServices({
      demoMode: true,
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

  it('prefers the explicit Ravn persona config over the shared API base', () => {
    buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        'ravn.personas': { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn/personas' },
      },
    } as any);

    expect(ravnMocks.buildRavnPersonaAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1',
    });
  });

  it('uses the explicit personas service base for the persona adapter when present', () => {
    buildServices({
      demoMode: true,
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
      trackerIssue: {
        id: 'issue-754',
        identifier: 'NIU-754',
        title: 'Canonical routes cleanup',
        status: 'in_progress',
        url: 'https://linear.app/niuu/issue/NIU-754',
      },
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
      trackerIssue: {
        id: 'issue-753',
        identifier: 'NIU-753',
        title: 'Legacy shim cleanup',
        status: 'done',
        url: 'https://linear.app/niuu/issue/NIU-753',
      },
      activityState: null,
      archivedAt: new Date('2026-04-23T13:00:00Z'),
    };
    const liveVolundr = {
      kind: 'volundr',
      getTargets: vi.fn().mockResolvedValue([{ id: 'thor', name: 'Thor', available: true }]),
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
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
      },
    } as any);

    expect(services['volundr.sessions']).toBe(services.sessionStore);

    const sessionStore = services.sessionStore as any;
    expect(sessionStore.listRequestTimeoutMs).toBe(8000);
    await expect(sessionStore.listSources()).resolves.toEqual([{ id: 'thor', name: 'Thor' }]);
    const signal = new AbortController().signal;
    await expect(sessionStore.listSessions({ instanceId: 'thor' }, signal)).resolves.toEqual([
      expect.objectContaining({ id: 'sess-live' }),
    ]);
    expect(liveVolundr.getSessions).toHaveBeenLastCalledWith({ instanceId: 'thor', signal });
    await expect(
      sessionStore.listSessions({ instanceId: 'thor', archivedOnly: true }, signal),
    ).resolves.toEqual([expect.objectContaining({ id: 'sess-archived', state: 'archived' })]);
    expect(liveVolundr.listArchivedSessions).toHaveBeenLastCalledWith({
      instanceId: 'thor',
      signal,
    });
    await expect(
      sessionStore.listSessions({ instanceId: 'thor', state: 'archived' }, signal),
    ).resolves.toEqual([]);
    await expect(sessionStore.listSessions()).resolves.toEqual([
      expect.objectContaining({
        id: 'sess-live',
        name: 'feat/canonical-routes',
        title: 'Canonical routes cleanup',
        ravnId: 'NIU-754',
        personaName: 'feat/canonical-routes',
        trackerIssue: expect.objectContaining({
          identifier: 'NIU-754',
          title: 'Canonical routes cleanup',
        }),
        templateId: 'forge-web',
        clusterId: 'shared',
        state: 'running',
        tokensIn: 4200,
        tokensOut: 0,
        preview: 'github.com/niuulabs/volundr#feat/canonical-routes',
      }),
      expect.objectContaining({
        id: 'sess-archived',
        name: 'fix/legacy-shim-cleanup',
        title: 'Legacy shim cleanup',
        ravnId: 'NIU-753',
        trackerIssue: expect.objectContaining({
          identifier: 'NIU-753',
          title: 'Legacy shim cleanup',
        }),
        state: 'archived',
        terminatedAt: '2026-04-23T13:00:00.000Z',
      }),
    ]);
    await expect(sessionStore.listSessions({ state: 'archived' })).resolves.toEqual([
      expect.objectContaining({ id: 'sess-archived' }),
    ]);
    await expect(sessionStore.getSession('sess-archived')).resolves.toEqual(
      expect.objectContaining({
        id: 'sess-archived',
        title: 'Legacy shim cleanup',
        trackerIssue: expect.objectContaining({ identifier: 'NIU-753' }),
        state: 'archived',
      }),
    );
    await expect(
      sessionStore.getSession('sess-archived', { instanceId: 'thor', signal }),
    ).resolves.toEqual(expect.objectContaining({ id: 'sess-archived' }));
    expect(liveVolundr.getSession).toHaveBeenLastCalledWith('sess-archived', {
      instanceId: 'thor',
      signal,
    });
    expect(liveVolundr.listArchivedSessions).toHaveBeenLastCalledWith({
      instanceId: 'thor',
      signal,
    });
    await sessionStore.deleteSession('sess-live');
    expect(liveVolundr.deleteSession).toHaveBeenCalledWith('sess-live', undefined);
    expect(sessionStore.sessionChanges).toBeUndefined();
  });

  it('reads one host incrementally when the Forge adapter can, on the configured bound', async () => {
    const liveVolundr = {
      kind: 'volundr',
      getTargets: vi.fn().mockResolvedValue([]),
      getSessionChanges: vi.fn().mockResolvedValue({
        cursor: '12',
        ids: ['sess-live'],
        sessions: [
          {
            id: 'sess-live',
            name: 'live',
            source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
            status: 'running',
            model: 'claude-sonnet',
            lastActive: Date.parse('2026-04-23T12:00:00Z'),
            messageCount: 1,
            tokensUsed: 10,
          },
        ],
      }),
      subscribe: vi.fn(() => () => {}),
    };
    volundrMocks.buildVolundrHttpAdapter.mockReturnValue(liveVolundr as any);
    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        forge: {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/forge',
          sessionListFullResyncMs: 120_000,
        },
      },
    } as any);
    const reader = (services.sessionStore as any).sessionChanges;
    expect(reader.fullResyncMs).toBe(120_000);
    const signal = new AbortController().signal;

    const changes = await reader.read('10', { instanceId: 'thor', archivedOnly: true }, signal);

    expect(liveVolundr.getSessionChanges).toHaveBeenLastCalledWith('10', {
      instanceId: 'thor',
      archived: true,
      signal,
    });
    expect(changes.cursor).toBe('12');
    expect(changes.ids).toEqual(['sess-live']);
    expect(changes.sessions).toEqual([expect.objectContaining({ id: 'sess-live' })]);
  });

  it('maps an awaiting_input running session to the awaiting_input state', async () => {
    const blocked = {
      id: 'sess-blocked',
      name: 'fix-auth',
      source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
      status: 'running',
      model: 'claude-sonnet',
      lastActive: Date.parse('2026-04-24T12:30:00Z'),
      messageCount: 1,
      tokensUsed: 10,
      activityState: 'awaiting_input',
      needsAttention: true,
    };
    const liveVolundr = {
      kind: 'volundr',
      getSessions: vi.fn().mockResolvedValue([blocked]),
      getSession: vi.fn().mockResolvedValue(blocked),
      listArchivedSessions: vi.fn().mockResolvedValue([]),
      deleteSession: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn(() => () => {}),
    };
    volundrMocks.buildVolundrHttpAdapter.mockReturnValue(liveVolundr as any);

    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: { forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' } },
    } as any);

    const sessionStore = services.sessionStore as any;
    await expect(sessionStore.listSessions()).resolves.toEqual([
      expect.objectContaining({ id: 'sess-blocked', state: 'awaiting_input' }),
    ]);
  });

  it('gives a session with no repository no preview, not a bare "#"', async () => {
    const repoless = {
      id: 'sess-repoless',
      name: 'tool-build',
      source: { type: 'git', repo: '', branch: '' },
      status: 'stopped',
      model: 'claude-sonnet',
      lastActive: Date.parse('2026-04-24T12:30:00Z'),
      messageCount: 1,
      tokensUsed: 10,
      activityState: null,
    };
    const branchless = {
      ...repoless,
      id: 'sess-branchless',
      source: { type: 'git', repo: 'r', branch: '' },
    };
    const liveVolundr = {
      kind: 'volundr',
      getSessions: vi.fn().mockResolvedValue([repoless, branchless]),
      getSession: vi.fn().mockResolvedValue(repoless),
      listArchivedSessions: vi.fn().mockResolvedValue([]),
      deleteSession: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn(() => () => {}),
    };
    volundrMocks.buildVolundrHttpAdapter.mockReturnValue(liveVolundr as any);

    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: { forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' } },
    } as any);

    const [first, second] = await (services.sessionStore as any).listSessions();
    expect(first.preview).toBeUndefined();
    expect(second.preview).toBe('r');
  });

  it('prefers an explicit forge service base for the main Volundr http adapter', () => {
    buildServices({
      demoMode: true,
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
      undefined,
      expect.objectContaining({
        niuuBasePath: 'http://localhost:8080/api/v1/niuu',
      }),
    );
  });

  it('builds a live cluster adapter from Volundr resources and sessions', async () => {
    const liveVolundr = {
      kind: 'volundr',
      getSessions: vi.fn().mockResolvedValue([
        {
          id: 'sess-running',
          name: 'agent-runtime',
          instanceName: 'Valhalla',
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
          instanceName: 'Noatun',
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
            instanceSlug: 'valhalla',
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
            instanceSlug: 'noatun',
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
      getTargets: vi.fn().mockResolvedValue([
        {
          id: 'target-noatun',
          slug: 'noatun',
          name: 'Noatun',
          baseUrl: 'https://niuu.noatun.asgard.niuu.world',
          enabled: true,
          isDefault: true,
          visibility: 'system',
          tags: ['noatun', 'cpu'],
        },
        {
          id: 'target-valhalla',
          slug: 'valhalla',
          name: 'Valhalla',
          baseUrl: 'https://volundr.valhalla.asgard.niuu.world',
          enabled: true,
          isDefault: false,
          visibility: 'system',
          tags: ['valhalla', 'gpu'],
        },
      ]),
      getLaunchSpecs: vi.fn().mockResolvedValue([]),
      getLaunchSpec: vi.fn().mockResolvedValue(null),
      listArchivedSessions: vi.fn().mockResolvedValue([]),
      deleteSession: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn(() => () => {}),
    };
    volundrMocks.buildVolundrHttpAdapter.mockReturnValue(liveVolundr as any);

    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
      },
    } as any);

    const clusterAdapter = services['volundr.clusters'] as any;
    await expect(clusterAdapter.getClusters()).resolves.toEqual([
      expect.objectContaining({
        id: 'target-noatun',
        name: 'Noatun',
        kind: 'primary',
        region: 'noatun',
        capacity: { cpu: 4, memMi: 8192, gpu: 0 },
        used: { cpu: 0.5, memMi: 1024, gpu: 0 },
        runningSessions: 0,
        queuedProvisions: 1,
        pods: [
          expect.objectContaining({
            name: 'queued-runtime',
            status: 'pending',
            startedAt: '1970-01-01T00:00:00.000Z',
          }),
        ],
        nodes: [{ id: 'node-b', status: 'ready', role: 'control-plane' }],
      }),
      expect.objectContaining({
        id: 'target-valhalla',
        name: 'Valhalla',
        kind: 'gpu',
        region: 'ca-hamilton-1',
        capacity: { cpu: 8, memMi: 16384, gpu: 1 },
        used: { cpu: 1.5, memMi: 8192, gpu: 1 },
        runningSessions: 1,
        queuedProvisions: 0,
        pods: [
          expect.objectContaining({
            name: 'forge-pod-1',
            status: 'running',
            startedAt: '2026-04-24T12:30:00.000Z',
          }),
        ],
        nodes: [{ id: 'node-a', status: 'ready', role: 'worker' }],
      }),
    ]);
    expect(services.clusterAdapter).toBe(services['volundr.clusters']);
    await expect(clusterAdapter.getCluster('target-valhalla')).resolves.toEqual(
      expect.objectContaining({ id: 'target-valhalla' }),
    );
  });

  it('returns no live clusters when Volundr exposes neither nodes nor sessions', async () => {
    const liveVolundr = {
      kind: 'volundr',
      getSessions: vi.fn().mockResolvedValue([]),
      getSession: vi.fn().mockResolvedValue(null),
      getClusterResources: vi.fn().mockResolvedValue({ resourceTypes: [], nodes: [] }),
      getLaunchSpecs: vi.fn().mockResolvedValue([]),
      getLaunchSpec: vi.fn().mockResolvedValue(null),
      listArchivedSessions: vi.fn().mockResolvedValue([]),
      deleteSession: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn(() => () => {}),
    };
    volundrMocks.buildVolundrHttpAdapter.mockReturnValue(liveVolundr as any);

    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
      },
    } as any);

    const clusterAdapter = services['volundr.clusters'] as any;
    await expect(clusterAdapter.getClusters()).resolves.toEqual([]);
    await expect(clusterAdapter.getCluster('shared')).resolves.toBeNull();
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
      getLaunchSpecs: vi.fn().mockResolvedValue([]),
      getLaunchSpec: vi.fn().mockResolvedValue(null),
      listArchivedSessions: vi.fn().mockResolvedValue([]),
      deleteSession: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn(() => () => {}),
    };
    volundrMocks.buildVolundrHttpAdapter.mockReturnValue(liveVolundr as any);

    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
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
    await expect(clusterAdapter.getClusters()).rejects.toThrow('cluster resources unavailable');
  });

  it('keeps mock session stores when Volundr is not live', () => {
    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {},
    } as any);

    expect(volundrMocks.createMockSessionStore).toHaveBeenCalledTimes(1);
    expect(volundrMocks.createMockClusterAdapter).toHaveBeenCalledTimes(1);
    expect(services).not.toHaveProperty('volundr.templates');
    expect((services.features as any).kind).toBe('mock-feature-catalog');
    expect((services.identity as any).kind).toBe('mock-identity');
    expect((services['volundr.sessions'] as any).kind).toBe('mock-session-store');
    expect((services.sessionStore as any).kind).toBe('mock-session-store');
    expect((services['volundr.clusters'] as any).kind).toBe('mock-clusters');
    expect((services.clusterAdapter as any).kind).toBe('mock-clusters');
    expect((services['volundr.notifications'] as any).kind).toBe('mock-notifications');
  });

  it('builds the notifications feed on the Forge base, sharing the Forge fleet stream', () => {
    const forgeAdapter = { kind: 'forge-http', subscribeForgeStream: vi.fn() };
    // The Forge runtime adapter is built first, before the catalog adapter.
    volundrMocks.buildVolundrHttpAdapter.mockReturnValueOnce(forgeAdapter as any);
    const services = buildServices({
      theme: 'ice',
      plugins: {},
      services: { forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' } },
    } as any);
    const feed = services['volundr.notifications'] as any;
    expect(feed.kind).toBe('notifications');
    expect(feed.client).toEqual({ basePath: 'http://localhost:8080/api/v1/forge' });
    expect(volundrMocks.forgeApiBasePath).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/forge',
    );
    // The feed listens on the Forge adapter itself, not on a second stream.
    expect(feed.options.stream).toBe(forgeAdapter);
  });

  it('leaves notifications unavailable without a Forge or demo mode', () => {
    const services = buildServices({ theme: 'ice', plugins: {}, services: {} } as any);
    expect(isUnavailableService(services['volundr.notifications'])).toBe(true);
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
    volundrMocks.buildVolundrHttpAdapter.mockReturnValueOnce(liveVolundr as any);

    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
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
        startup: null,
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
    await expect(sessionStore.createSession({})).rejects.toBeInstanceOf(
      UnsupportedSessionStoreOperationError,
    );
    await expect(sessionStore.updateSession('sess-idle', {})).rejects.toBeInstanceOf(
      UnsupportedSessionStoreOperationError,
    );

    const unsubscribe = sessionStore.subscribe(vi.fn());
    unsubscribe();
  });

  it('builds live stream and observatory adapters when those backends are configured', () => {
    buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        'forge.pty': { mode: 'ws', wsUrl: 'ws://localhost:8080/api/v1/forge/pty/{sessionId}' },
        'forge.metrics': {
          mode: 'http',
          baseUrl: 'http://localhost:8080/api/v1/forge/metrics',
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
      urlTemplate: 'ws://localhost:8080/api/v1/forge/pty/{sessionId}',
    });
    expect(volundrMocks.buildVolundrMetricsSseAdapter).toHaveBeenCalledWith({
      urlTemplate: 'http://localhost:8080/api/v1/forge/metrics',
    });
    expect(queryMocks.createApiClient).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/observatory',
    );
    expect(observatoryMocks.buildObservatoryRegistryHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/observatory',
    });
    // Topology reads the merged Guild aggregate, not one cluster's feed, so it
    // is built from an ApiClient rather than a stream URL.
    expect(observatoryMocks.buildObservatoryTopologyAggregateAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/observatory/topology',
    });
    expect(observatoryMocks.buildObservatoryEventsSseStream).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/observatory/events',
    );
  });

  it('derives the bundled host pty websocket path from the live forge base when no explicit pty config exists', () => {
    buildServices({
      demoMode: true,
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

  it('lets a grouped observatory base drive all observatory adapters by default', () => {
    buildServices({
      demoMode: true,
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
    // Topology reads the merged Guild aggregate, not one cluster's feed, so it
    // is built from an ApiClient rather than a stream URL.
    expect(observatoryMocks.buildObservatoryTopologyAggregateAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/observatory/topology',
    });
    expect(observatoryMocks.buildObservatoryEventsSseStream).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/observatory/events',
    );
    expect(observatoryMocks.buildObservatoryAgentDirectoryHttpAdapter).toHaveBeenCalledWith({
      basePath: 'http://localhost:8080/api/v1/observatory',
    });
  });

  it('normalizes an explicit aggregate agent directory endpoint to its service root', () => {
    const services = buildServices({
      demoMode: false,
      theme: 'ice',
      plugins: {},
      services: {
        'observatory.agents': {
          mode: 'http',
          baseUrl: 'https://guild.example.test/api/v1/niuu/observatory/agents',
        },
      },
    } as any);

    expect(queryMocks.createApiClient).toHaveBeenCalledWith(
      'https://guild.example.test/api/v1/niuu/observatory',
    );
    expect(observatoryMocks.buildObservatoryAgentDirectoryHttpAdapter).toHaveBeenCalledWith({
      basePath: 'https://guild.example.test/api/v1/niuu/observatory',
    });
    expect(services['observatory.agents']).toEqual({
      kind: 'observatory-agents',
      client: { basePath: 'https://guild.example.test/api/v1/niuu/observatory' },
    });
  });

  it('prefers explicit observatory surface overrides over the grouped observatory base', () => {
    buildServices({
      demoMode: true,
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
      demoMode: true,
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

  it('registers the realm governance mock by default', () => {
    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {},
    } as any);

    expect(services['realms.governance']).toEqual({ kind: 'mock-realms' });
  });

  it('wires realm governance to the shared API base', () => {
    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
      },
    } as any);

    expect(realmsMocks.buildRealmGovernanceHttpAdapter).toHaveBeenCalledWith(
      { basePath: 'http://localhost:8080/api/v1' },
      'http://localhost:8080/api/v1',
    );
    expect(services['realms.governance']).toMatchObject({ kind: 'realms' });
  });

  it('derives the realm governance base from the Forge base when Work is not wired', () => {
    const services = buildServices({
      demoMode: true,
      theme: 'ice',
      plugins: {},
      services: {
        forge: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/forge' },
      },
    } as any);

    expect(services['realms.governance']).toMatchObject({
      kind: 'realms',
      realmsStreamBase: 'http://localhost:8080/api/v1',
    });
  });
});

it('uses Guild for knowledge deployment discovery and actions', async () => {
  const { buildMimirHttpAdapter } = await import('@niuulabs/plugin-mimir');
  buildServices({
    demoMode: true,
    theme: 'ice',
    plugins: {},
    services: {
      niuu: { mode: 'http', baseUrl: 'https://guild.test/api/v1/niuu' },
      mimir: { mode: 'http', baseUrl: 'https://memory.test/api/v1/mimir' },
    },
  } as any);
  expect(buildMimirHttpAdapter).toHaveBeenCalledWith(
    { basePath: 'https://memory.test/api/v1/mimir' },
    { basePath: 'https://guild.test/api/v1/niuu/knowledge' },
  );
});
