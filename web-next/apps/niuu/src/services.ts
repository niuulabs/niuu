import { buildBifrostHttpAdapter, createMockBifrostService } from '@niuulabs/plugin-bifrost';
import type { IBifrostService } from '@niuulabs/plugin-bifrost';
import {
  createMockPersonaStore,
  createMockRavenStream,
  createMockSessionStream,
  createMockTriggerStore,
  createMockBudgetStream,
  createMockWardenStore,
  buildRavnPersonaAdapter,
  buildRavnRavenAdapter,
  buildRavnResidentControlAdapter,
  buildRavnSessionAdapter,
  buildRavnTriggerAdapter,
  buildRavnBudgetAdapter,
  buildRavnWardenAdapter,
  type IResidentControl,
} from '@niuulabs/plugin-ravn';
import {
  createMockTingService,
  createMockDispatcherService,
  createMockTingSessionService,
  createMockTrackerService,
  createMockWorkflowService,
  createMockResearchService,
  createMockSpecsService,
  createMockDispatchBus,
  createMockTingSettingsService,
  createMockAuditLogService,
  buildTingHttpAdapter,
  buildDispatcherHttpAdapter,
  buildTingSessionHttpAdapter,
  buildTrackerHttpAdapter,
  buildWorkflowHttpAdapter,
  buildResearchHttpAdapter,
  buildSpecsHttpAdapter,
  buildDispatchBusHttpAdapter,
  buildTingSettingsHttpAdapter,
  buildTingAuditLogHttpAdapter,
} from '@niuulabs/plugin-ting';
import { createMimirMockAdapter, buildMimirHttpAdapter } from '@niuulabs/plugin-mimir';
import {
  createMockAgentDirectory,
  createMockRegistryRepository,
  createMockTopologyStream,
  createMockEventStream,
  buildObservatoryRegistryHttpAdapter,
  buildObservatoryTopologyAggregateAdapter,
  buildObservatoryEventsSseStream,
  buildObservatoryAgentDirectoryHttpAdapter,
} from '@niuulabs/plugin-observatory';
import {
  createMockVolundrService,
  createMockClusterAdapter,
  createMockSessionStore,
  buildVolundrHttpAdapter,
  buildVolundrFileSystemHttpAdapter,
  createMockPtyStream,
  createMockMetricsStream,
  createMockFileSystemPort,
  buildVolundrPtyWsAdapter,
  buildVolundrMetricsSseAdapter,
  type IClusterAdapter,
  type Cluster,
  type IVolundrService,
  type ISessionStore,
  type Session,
  type SessionFilters,
  type VolundrSession,
} from '@niuulabs/plugin-volundr';
import {
  buildOdinReviewHttpAdapter,
  buildRealmGovernanceHttpAdapter,
  buildValkyrieHttpAdapter,
  buildValkyrieSkillsHttpAdapter,
  createMockOdinReviewService,
  createMockRealmGovernanceService,
  createMockValkyrieService,
  createMockValkyrieSkillsService,
} from '@niuulabs/plugin-valkyrie';
import { createApiClient } from '@niuulabs/query';
import {
  buildFeatureCatalogAdapter,
  buildIdentityAdapter,
  createMockFeatureCatalogService,
  createMockIdentityService,
  type IFeatureCatalogService,
  type IIdentityService,
} from '@niuulabs/plugin-sdk';
import type { NiuuConfig, ServiceConfig, ServicesMap } from '@niuulabs/plugin-sdk';
import { buildRepoCatalogHttpAdapter, createMockRepoCatalogService } from './repoCatalog';

export interface ServiceBackendStatus {
  mode: 'live' | 'demo' | 'unavailable';
  transport: 'http' | 'ws' | 'mock' | 'none';
  target: string | null;
  source: string;
  note?: string;
}

export class UnsupportedSessionStoreOperationError extends Error {
  readonly operation: 'create' | 'update';

  constructor(operation: 'create' | 'update') {
    super(
      `The app session-store adapter does not support session ${operation}. Use the Forge lifecycle service instead.`,
    );
    this.name = 'UnsupportedSessionStoreOperationError';
    this.operation = operation;
  }
}

export interface UnavailableServiceStatus {
  available: false;
  serviceName: string;
  reason: string;
}

export class ServiceUnavailableError extends Error {
  readonly serviceName: string;

  constructor(serviceName: string, reason = 'No live backend is configured.') {
    super(`Service "${serviceName}" is unavailable: ${reason}`);
    this.name = 'ServiceUnavailableError';
    this.serviceName = serviceName;
  }
}

export const UNAVAILABLE_SERVICE = Symbol('niuu.unavailable-service');

export function isUnavailableService(
  service: unknown,
): service is { [UNAVAILABLE_SERVICE]: UnavailableServiceStatus } {
  return Boolean(
    service &&
    typeof service === 'object' &&
    UNAVAILABLE_SERVICE in service &&
    (service as { [UNAVAILABLE_SERVICE]?: UnavailableServiceStatus })[UNAVAILABLE_SERVICE]
      ?.available === false,
  );
}

function unavailableService<T>(serviceName: string): T {
  const reason = 'No live backend is configured. Enable demoMode for synthetic data.';
  const status: UnavailableServiceStatus = { available: false, serviceName, reason };
  return new Proxy(
    { [UNAVAILABLE_SERVICE]: status } as unknown as T & Record<PropertyKey, unknown>,
    {
      get(target, property, receiver) {
        if (property === 'then') return undefined;
        if (property in target) return Reflect.get(target, property, receiver);
        return () => {
          throw new ServiceUnavailableError(serviceName, reason);
        };
      },
    },
  );
}

function demoService<T>(config: Partial<NiuuConfig>, serviceName: string, factory: () => T): T {
  if (config.demoMode) return factory();
  return unavailableService<T>(serviceName);
}

/**
 * A service config is "live" (i.e. should use a real transport) when its mode
 * is `http` or `ws` and a URL is present. Any other combination — missing
 * mode or a missing URL is unavailable unless the runtime explicitly enables demo mode.
 */
function hasHttpBackend(
  svc: ServiceConfig | undefined,
): svc is ServiceConfig & { baseUrl: string } {
  return svc?.mode === 'http' && typeof svc.baseUrl === 'string';
}

function hasWsBackend(svc: ServiceConfig | undefined): svc is ServiceConfig & { wsUrl: string } {
  return svc?.mode === 'ws' && typeof svc.wsUrl === 'string';
}

function resolveBrowserRelativeWsUrl(url: string): string {
  if (!url.startsWith('/')) return url;
  if (typeof window === 'undefined') return url;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${url}`;
}

function resolveDirectServiceWsUrl(
  config: Pick<NiuuConfig, 'services'>,
  ...serviceKeys: string[]
): string | null {
  for (const serviceKey of serviceKeys) {
    const svc = config.services[serviceKey];
    if (hasWsBackend(svc)) return resolveBrowserRelativeWsUrl(svc.wsUrl);
  }
  return null;
}

function resolveDirectServiceBase(
  config: Pick<NiuuConfig, 'services'>,
  ...serviceKeys: string[]
): string | null {
  for (const serviceKey of serviceKeys) {
    const svc = config.services[serviceKey];
    if (hasHttpBackend(svc)) return svc.baseUrl;
  }
  return null;
}

function resolveDirectServiceStatus(
  config: Pick<NiuuConfig, 'services'>,
  transport: 'http' | 'ws',
  ...serviceKeys: string[]
): ServiceBackendStatus {
  for (const serviceKey of serviceKeys) {
    const svc = config.services[serviceKey];
    if (transport === 'http' && hasHttpBackend(svc)) {
      return {
        mode: 'live',
        transport,
        target: svc.baseUrl,
        source: serviceKey,
      };
    }
    if (transport === 'ws' && hasWsBackend(svc)) {
      return {
        mode: 'live',
        transport,
        target: resolveBrowserRelativeWsUrl(svc.wsUrl),
        source: serviceKey,
      };
    }
  }
  return {
    mode: 'unavailable',
    transport: 'none',
    target: null,
    source: 'configuration',
  };
}

export function toSharedApiBase(baseUrl: string): string {
  return baseUrl.replace(/\/(?:ting|forge|volundr)\/?$/, '');
}

export function toHostBase(baseUrl: string): string {
  return baseUrl.replace(/\/api\/v1\/forge\/?$/, '');
}

export function toHostPtyWsUrl(baseUrl: string): string {
  const hostBase = toHostBase(baseUrl)
    .replace(/^http:/, 'ws:')
    .replace(/^https:/, 'wss:');
  return `${hostBase}/s/{sessionId}/session`;
}

export function resolveSharedApiBase(config: Pick<NiuuConfig, 'services'>): string | null {
  const tingSvc = config.services['ting'];
  if (hasHttpBackend(tingSvc)) return toSharedApiBase(tingSvc.baseUrl);

  const forgeSvc = config.services['forge'];
  if (hasHttpBackend(forgeSvc)) return toSharedApiBase(forgeSvc.baseUrl);

  const volundrSvc = config.services['volundr'];
  if (hasHttpBackend(volundrSvc)) return toSharedApiBase(volundrSvc.baseUrl);

  return null;
}

export function resolveNiuuRegistryBase(config: Pick<NiuuConfig, 'services'>): string | null {
  const explicitBase = resolveDirectServiceBase(config, 'niuu');
  if (explicitBase) return explicitBase.replace(/\/$/, '');

  const sharedBase = resolveSharedApiBase(config);
  return sharedBase ? `${sharedBase}/niuu` : null;
}

export function resolveCanonicalServiceBase(
  config: Pick<NiuuConfig, 'services'>,
  serviceKey: string,
): string | null {
  const explicitBase = resolveDirectServiceBase(config, serviceKey);
  if (explicitBase) return explicitBase;
  return resolveSharedApiBase(config);
}

export function resolveForgeServiceBase(config: Pick<NiuuConfig, 'services'>): string | null {
  return resolveDirectServiceBase(config, 'forge');
}

function resolveRepoCatalogBase(config: Pick<NiuuConfig, 'services'>): string | null {
  const explicitBase = resolveDirectServiceBase(config, 'niuu.repos', 'niuu');
  if (explicitBase) return explicitBase.replace(/\/repos\/?$/, '');

  const sharedBase = resolveSharedApiBase(config);
  return sharedBase ? `${sharedBase}/niuu` : null;
}

function resolveRepoCatalogStatus(config: Pick<NiuuConfig, 'services'>): ServiceBackendStatus {
  const explicit = resolveDirectServiceStatus(config, 'http', 'niuu.repos', 'niuu');
  if (explicit.mode === 'live' && explicit.target) {
    return { ...explicit, target: explicit.target.replace(/\/repos\/?$/, '') };
  }

  const base = resolveRepoCatalogBase(config);
  if (!base) {
    return {
      mode: 'unavailable',
      transport: 'none',
      target: null,
      source: 'configuration',
    };
  }

  return {
    mode: 'live',
    transport: 'http',
    target: base,
    source: 'shared-api',
  };
}

function resolveVolundrServiceBase(config: Pick<NiuuConfig, 'services'>): string | null {
  const explicitBase = resolveDirectServiceBase(config, 'volundr');
  if (explicitBase) return explicitBase;

  const sharedBase = resolveSharedApiBase(config);
  return sharedBase ? `${sharedBase}/volundr` : null;
}

function resolveBifrostServiceBase(config: Pick<NiuuConfig, 'services'>): string | null {
  const explicitBase = resolveDirectServiceBase(config, 'bifrost');
  if (explicitBase) return explicitBase;

  const sharedBase = resolveSharedApiBase(config);
  return sharedBase ? `${sharedBase}/bifrost` : null;
}

function resolveCredentialsServiceBase(config: Pick<NiuuConfig, 'services'>): string | null {
  const explicitBase = resolveDirectServiceBase(config, 'credentials');
  if (explicitBase) return explicitBase;

  const sharedBase = resolveSharedApiBase(config);
  return sharedBase ? `${sharedBase}/credentials` : null;
}

function resolveIntegrationsServiceBase(config: Pick<NiuuConfig, 'services'>): string | null {
  const explicitBase = resolveDirectServiceBase(config, 'integrations');
  if (explicitBase) return explicitBase;

  const sharedBase = resolveSharedApiBase(config);
  return sharedBase ? `${sharedBase}/integrations` : null;
}

function resolveForgeStreamWsUrl(config: Pick<NiuuConfig, 'services'>): string | null {
  const explicitWsUrl = resolveDirectServiceWsUrl(config, 'forge.pty');
  if (explicitWsUrl) return explicitWsUrl;

  const forgeBase = resolveForgeServiceBase(config);
  return forgeBase ? toHostPtyWsUrl(forgeBase) : null;
}

function resolveForgeMetricsBase(config: Pick<NiuuConfig, 'services'>): string | null {
  return resolveDirectServiceBase(config, 'forge.metrics');
}

function resolveFilesystemBase(config: Pick<NiuuConfig, 'services'>): string | null {
  const explicitBase = resolveDirectServiceBase(config, 'filesystem');
  if (explicitBase) return explicitBase;

  return resolveForgeServiceBase(config);
}

function resolveFilesystemStatus(config: Pick<NiuuConfig, 'services'>): ServiceBackendStatus {
  const explicit = resolveDirectServiceStatus(config, 'http', 'filesystem');
  if (explicit.mode === 'live') return explicit;

  const explicitForgeBase = resolveDirectServiceBase(config, 'forge');
  if (explicitForgeBase) {
    return {
      mode: 'live',
      transport: 'http',
      target: explicitForgeBase,
      source: 'forge',
    };
  }

  const sharedBase = resolveSharedApiBase(config);
  if (sharedBase) {
    return {
      mode: 'live',
      transport: 'http',
      target: `${sharedBase}/forge`,
      source: 'shared-api',
    };
  }

  return {
    mode: 'unavailable',
    transport: 'none',
    target: null,
    source: 'configuration',
    note: 'No live filesystem API is wired yet.',
  };
}

function resolveTingServiceBase(
  config: Pick<NiuuConfig, 'services'>,
  serviceKey:
    | 'ting'
    | 'ting.dispatcher'
    | 'ting.sessions'
    | 'ting.dispatch'
    | 'ting.settings'
    | 'ting.workflows'
    | 'ting.research'
    | 'ting.specs',
): string | null {
  const explicitBase = resolveDirectServiceBase(config, serviceKey);
  if (!explicitBase) return resolveDirectServiceBase(config, 'ting');

  switch (serviceKey) {
    case 'ting.dispatcher':
      return explicitBase.replace(/\/dispatcher\/?$/, '');
    case 'ting.sessions':
      return explicitBase.replace(/\/sessions\/?$/, '');
    case 'ting.dispatch':
      return explicitBase.replace(/\/dispatch\/?$/, '');
    case 'ting.settings':
      return explicitBase.replace(/\/settings\/?$/, '');
    case 'ting.workflows':
      return explicitBase.replace(/\/workflows\/?$/, '');
    case 'ting.research':
      return explicitBase.replace(/\/research\/?$/, '');
    case 'ting.specs':
      return explicitBase.replace(/\/specs\/?$/, '');
    default:
      return explicitBase;
  }
}

function resolveObservatoryServiceBase(
  config: Pick<NiuuConfig, 'services'>,
  serviceKey:
    'observatory.registry' | 'observatory.topology' | 'observatory.events' | 'observatory.agents',
): string | null {
  const explicitBase = resolveDirectServiceBase(config, serviceKey);
  if (explicitBase) {
    if (serviceKey === 'observatory.registry') {
      return explicitBase.replace(/\/registry\/?$/, '');
    }
    if (serviceKey === 'observatory.agents') {
      return explicitBase.replace(/\/agents\/?$/, '');
    }
    return explicitBase;
  }

  // Both of these are estate-wide: the Guild merges every cluster plus every
  // source that pushes instead of being polled. A single cluster's endpoint
  // would render a fraction of the graph and give no sign the rest exists.
  if (serviceKey === 'observatory.agents' || serviceKey === 'observatory.topology') {
    const aggregateBase = resolveNiuuRegistryBase(config);
    if (aggregateBase) return `${aggregateBase}/observatory`;
  }

  const groupedBase = resolveDirectServiceBase(config, 'observatory');
  if (!groupedBase) return null;

  if (serviceKey === 'observatory.registry' || serviceKey === 'observatory.agents') {
    return groupedBase;
  }
  if (serviceKey === 'observatory.topology') return `${groupedBase}/topology`;
  return `${groupedBase}/events`;
}

function resolveValkyrieServiceBase(
  config: Pick<NiuuConfig, 'services'>,
  serviceKey: 'valkyrie' | 'valkyrie.reviews' | 'valkyrie.skills',
): string | null {
  const explicitBase = resolveDirectServiceBase(config, serviceKey);
  if (explicitBase) return explicitBase;

  const groupedBase = resolveDirectServiceBase(config, 'valkyrie');
  if (!groupedBase) return null;
  // The review queue lives beside the dashboard API under /ravn/odin;
  // learned skills ride the dashboard base (`<valkyrieBase>/skills`).
  return serviceKey === 'valkyrie.reviews'
    ? groupedBase.replace(/\/valkyrie\/?$/, '/odin')
    : groupedBase;
}

/**
 * Realm governance spans two backends: realms on the shared niuu host
 * (`<sharedApiBase>/realms`) and workflows on Ting (`<tingBase>/workflows`).
 * Live only when both are reachable.
 */
function resolveRealmGovernanceBases(
  config: Pick<NiuuConfig, 'services'>,
): { realmsBase: string; workflowsBase: string } | null {
  const realmsBase = resolveSharedApiBase(config);
  const workflowsBase = resolveTingServiceBase(config, 'ting.workflows');
  if (!realmsBase || !workflowsBase) return null;
  return { realmsBase, workflowsBase };
}

function resolveRealmGovernanceStatus(config: Pick<NiuuConfig, 'services'>): ServiceBackendStatus {
  const bases = resolveRealmGovernanceBases(config);
  if (!bases) {
    return {
      mode: 'unavailable',
      transport: 'none',
      target: null,
      source: 'configuration',
    };
  }
  return {
    mode: 'live',
    transport: 'http',
    target: bases.realmsBase,
    source: 'shared-api',
  };
}

export function resolveSettingsServiceBase(
  config: Pick<NiuuConfig, 'services'>,
  providerId:
    | 'identity'
    | 'credentials'
    | 'integrations'
    | 'ting'
    | 'volundr'
    | 'mimir'
    | 'ravn'
    | 'observatory'
    | 'bifrost',
): string | null {
  switch (providerId) {
    case 'identity': {
      const base = resolveCanonicalServiceBase(config, 'identity');
      if (!base) return null;
      return /\/identity\/?$/.test(base) ? base : `${base.replace(/\/$/, '')}/identity`;
    }
    case 'credentials':
      return resolveCredentialsServiceBase(config);
    case 'integrations':
      return resolveIntegrationsServiceBase(config);
    case 'ting':
      return resolveTingServiceBase(config, 'ting.settings');
    case 'volundr':
      return resolveVolundrServiceBase(config);
    case 'mimir':
      return resolveDirectServiceBase(config, 'mimir');
    case 'ravn':
      return resolveDirectServiceBase(config, 'ravn');
    case 'observatory':
      return resolveDirectServiceBase(config, 'observatory');
    case 'bifrost':
      return resolveBifrostServiceBase(config);
    default:
      return null;
  }
}

function resolveRavnServiceBase(
  config: Pick<NiuuConfig, 'services'>,
  serviceKey:
    | 'ravn.personas'
    | 'ravn.ravens'
    | 'ravn.sessions'
    | 'ravn.triggers'
    | 'ravn.budget'
    | 'ravn.wardens',
): string | null {
  const explicitBase =
    serviceKey === 'ravn.personas'
      ? resolveDirectServiceBase(config, serviceKey, 'personas')
      : resolveDirectServiceBase(config, serviceKey);
  if (!explicitBase) {
    return serviceKey === 'ravn.personas'
      ? resolveSharedApiBase(config)
      : resolveDirectServiceBase(config, 'ravn');
  }

  switch (serviceKey) {
    case 'ravn.personas':
      return explicitBase.replace(/\/(?:ravn\/)?personas\/?$/, '');
    case 'ravn.ravens':
      return explicitBase.replace(/\/ravens\/?$/, '');
    case 'ravn.sessions':
      return explicitBase.replace(/\/sessions\/?$/, '');
    case 'ravn.triggers':
      return explicitBase.replace(/\/triggers\/?$/, '');
    case 'ravn.budget':
      return explicitBase.replace(/\/budget\/?$/, '');
    case 'ravn.wardens':
      return explicitBase.replace(/\/wardens\/?$/, '');
    default:
      return explicitBase;
  }
}

function resolveRavnServiceStatus(
  config: Pick<NiuuConfig, 'services'>,
  serviceKey:
    | 'ravn.personas'
    | 'ravn.ravens'
    | 'ravn.sessions'
    | 'ravn.triggers'
    | 'ravn.budget'
    | 'ravn.wardens',
): ServiceBackendStatus {
  const explicit =
    serviceKey === 'ravn.personas'
      ? resolveDirectServiceStatus(config, 'http', serviceKey, 'personas')
      : resolveDirectServiceStatus(config, 'http', serviceKey);
  if (explicit.mode === 'live' && explicit.target) {
    if (serviceKey === 'ravn.personas')
      return { ...explicit, target: explicit.target.replace(/\/(?:ravn\/)?personas\/?$/, '') };
    if (serviceKey === 'ravn.ravens')
      return { ...explicit, target: explicit.target.replace(/\/ravens\/?$/, '') };
    if (serviceKey === 'ravn.sessions')
      return { ...explicit, target: explicit.target.replace(/\/sessions\/?$/, '') };
    if (serviceKey === 'ravn.triggers')
      return { ...explicit, target: explicit.target.replace(/\/triggers\/?$/, '') };
    if (serviceKey === 'ravn.wardens')
      return { ...explicit, target: explicit.target.replace(/\/wardens\/?$/, '') };
    return { ...explicit, target: explicit.target.replace(/\/budget\/?$/, '') };
  }

  if (serviceKey === 'ravn.personas') {
    const sharedBase = resolveSharedApiBase(config);
    if (sharedBase) {
      return {
        mode: 'live',
        transport: 'http',
        target: sharedBase,
        source: 'shared-api',
      };
    }
  }

  return resolveDirectServiceStatus(config, 'http', 'ravn');
}

export function buildSharedFeatureCatalogService(
  config: Pick<NiuuConfig, 'services'>,
): IFeatureCatalogService {
  const featuresBase = resolveCanonicalServiceBase(config, 'features');
  if (!featuresBase) return demoService(config, 'features', createMockFeatureCatalogService);
  return buildFeatureCatalogAdapter(createApiClient(featuresBase));
}

export function buildSharedIdentityService(config: Pick<NiuuConfig, 'services'>): IIdentityService {
  const identityBase = resolveCanonicalServiceBase(config, 'identity');
  if (!identityBase) return demoService(config, 'identity', createMockIdentityService);
  return buildIdentityAdapter(createApiClient(identityBase));
}

function resolveCanonicalServiceStatus(
  config: Pick<NiuuConfig, 'services'>,
  serviceKey: string,
): ServiceBackendStatus {
  const explicit = resolveDirectServiceStatus(config, 'http', serviceKey);
  if (explicit.mode === 'live') return explicit;

  const sharedBase = resolveSharedApiBase(config);
  if (sharedBase) {
    return {
      mode: 'live',
      transport: 'http',
      target: sharedBase,
      source: 'shared-api',
    };
  }

  return explicit;
}

function resolveObservatoryServiceStatus(
  config: Pick<NiuuConfig, 'services'>,
  serviceKey:
    'observatory.registry' | 'observatory.topology' | 'observatory.events' | 'observatory.agents',
): ServiceBackendStatus {
  const explicit = resolveDirectServiceStatus(config, 'http', serviceKey);
  if (explicit.mode === 'live') {
    if (serviceKey === 'observatory.registry' && explicit.target) {
      return { ...explicit, target: explicit.target.replace(/\/registry\/?$/, '') };
    }
    if (serviceKey === 'observatory.agents' && explicit.target) {
      return { ...explicit, target: explicit.target.replace(/\/agents\/?$/, '') };
    }
    return explicit;
  }

  if (serviceKey === 'observatory.agents') {
    const aggregateBase = resolveNiuuRegistryBase(config);
    if (aggregateBase) {
      return {
        mode: 'live',
        transport: 'http',
        target: `${aggregateBase}/observatory`,
        source: 'niuu',
      };
    }
  }

  const grouped = resolveDirectServiceStatus(config, 'http', 'observatory');
  if (grouped.mode !== 'live' || !grouped.target) return grouped;

  if (serviceKey === 'observatory.registry' || serviceKey === 'observatory.agents') {
    return { ...grouped, source: 'observatory' };
  }
  if (serviceKey === 'observatory.topology') {
    return { ...grouped, target: `${grouped.target}/topology`, source: 'observatory' };
  }
  return { ...grouped, target: `${grouped.target}/events`, source: 'observatory' };
}

export function buildServiceBackendStatus(
  config: Pick<NiuuConfig, 'services' | 'demoMode'>,
): Record<string, ServiceBackendStatus> {
  const forgePtyStatus = resolveDirectServiceStatus(config, 'ws', 'forge.pty');
  const derivedForgeBase = resolveForgeServiceBase(config);

  const resolved: Record<string, ServiceBackendStatus> = {
    identity: resolveCanonicalServiceStatus(config, 'identity'),
    features: resolveCanonicalServiceStatus(config, 'features'),
    tracker: resolveCanonicalServiceStatus(config, 'tracker'),
    audit: resolveCanonicalServiceStatus(config, 'audit'),
    mimir: resolveDirectServiceStatus(config, 'http', 'mimir'),
    'observatory.registry': resolveObservatoryServiceStatus(config, 'observatory.registry'),
    'observatory.topology': resolveObservatoryServiceStatus(config, 'observatory.topology'),
    'observatory.events': resolveObservatoryServiceStatus(config, 'observatory.events'),
    'observatory.agents': resolveObservatoryServiceStatus(config, 'observatory.agents'),
    'ravn.personas': resolveRavnServiceStatus(config, 'ravn.personas'),
    'ravn.ravens': resolveRavnServiceStatus(config, 'ravn.ravens'),
    'ravn.residents': resolveRavnServiceStatus(config, 'ravn.ravens'),
    'ravn.sessions': resolveRavnServiceStatus(config, 'ravn.sessions'),
    'ravn.triggers': resolveRavnServiceStatus(config, 'ravn.triggers'),
    'ravn.budget': resolveRavnServiceStatus(config, 'ravn.budget'),
    'ravn.wardens': resolveRavnServiceStatus(config, 'ravn.wardens'),
    valkyrie: resolveDirectServiceStatus(config, 'http', 'valkyrie'),
    'valkyrie.reviews': resolveDirectServiceStatus(config, 'http', 'valkyrie.reviews', 'valkyrie'),
    'valkyrie.skills': resolveDirectServiceStatus(config, 'http', 'valkyrie.skills', 'valkyrie'),
    'valkyrie.realms': resolveRealmGovernanceStatus(config),
    'niuu.repos': resolveRepoCatalogStatus(config),
    forge: resolveDirectServiceStatus(config, 'http', 'forge'),
    'forge.pty':
      forgePtyStatus.mode === 'live'
        ? forgePtyStatus
        : derivedForgeBase
          ? {
              mode: 'live',
              transport: 'ws',
              target: toHostPtyWsUrl(derivedForgeBase),
              source: 'forge',
            }
          : forgePtyStatus,
    ting: resolveDirectServiceStatus(config, 'http', 'ting'),
    'ting.dispatcher': resolveDirectServiceStatus(config, 'http', 'ting.dispatcher', 'ting'),
    'ting.sessions': resolveDirectServiceStatus(config, 'http', 'ting.sessions', 'ting'),
    'ting.dispatch': resolveDirectServiceStatus(config, 'http', 'ting.dispatch', 'ting'),
    'ting.settings': resolveDirectServiceStatus(config, 'http', 'ting.settings', 'ting'),
    'ting.tracker': resolveCanonicalServiceStatus(config, 'tracker'),
    'ting.audit': resolveCanonicalServiceStatus(config, 'audit'),
    'ting.workflows': resolveDirectServiceStatus(config, 'http', 'ting.workflows', 'ting'),
    'ting.research': resolveDirectServiceStatus(config, 'http', 'ting.research', 'ting'),
    'ting.specs': resolveDirectServiceStatus(config, 'http', 'ting.specs', 'ting'),
    filesystem: resolveFilesystemStatus(config),
  };
  if (!config.demoMode) return resolved;

  return Object.fromEntries(
    Object.entries(resolved).map(([serviceName, status]) => [
      serviceName,
      status.mode === 'unavailable'
        ? {
            ...status,
            mode: 'demo',
            transport: 'mock',
            source: 'demo',
            note: status.note ?? 'Explicit demo adapter; no live backend is connected.',
          }
        : status,
    ]),
  );
}

const EMPTY_SESSION_RESOURCES: Session['resources'] = {
  cpuRequest: 0,
  cpuLimit: 0,
  cpuUsed: 0,
  memRequestMi: 0,
  memLimitMi: 0,
  memUsedMi: 0,
  gpuCount: 0,
};

function toIsoFromEpochMs(value: number | undefined): string | undefined {
  if (typeof value !== 'number' || Number.isNaN(value) || value <= 0) return undefined;
  return new Date(value).toISOString();
}

function toSessionState(session: VolundrSession): Session['state'] {
  switch (session.status) {
    case 'created':
      return 'requested';
    case 'starting':
    case 'provisioning':
      return 'provisioning';
    case 'running':
      if (session.needsAttention || session.activityState === 'awaiting_input')
        return 'awaiting_input';
      if (session.activityState === 'error') return 'failed';
      return session.activityState === 'idle' ? 'idle' : 'running';
    case 'stopping':
      return 'terminating';
    case 'stopped':
      return 'terminated';
    case 'archived':
      return 'archived';
    case 'failed':
    case 'error':
      return 'failed';
    default:
      return 'failed';
  }
}

function toSessionTemplateId(session: VolundrSession): string {
  if (session.taskType) return session.taskType;
  if (session.source.type === 'git') {
    const repoName = session.source.repo.split('/').pop();
    return repoName && repoName.length > 0 ? repoName : 'git';
  }
  return session.source.local_path ?? session.source.paths[0]?.mount_path ?? 'local-mount';
}

function toSessionClusterId(session: VolundrSession): string {
  return (
    session.instanceId ??
    session.instanceName ??
    session.podName ??
    session.hostname ??
    session.tenantId ??
    'shared'
  );
}

function toSessionRavnId(session: VolundrSession): string {
  return session.trackerIssue?.identifier ?? session.ownerId ?? session.tenantId ?? session.id;
}

function toSessionPreview(session: VolundrSession): string | undefined {
  if (session.source.type === 'git') {
    return `${session.source.repo}#${session.source.branch}`;
  }
  return session.source.local_path ?? session.source.paths[0]?.mount_path;
}

function toDomainSession(session: VolundrSession): Session {
  const startedAt = toIsoFromEpochMs(session.lastActive) ?? new Date(0).toISOString();
  const lastActivityAt = toIsoFromEpochMs(session.lastActive);
  const state = toSessionState(session);
  const readyAt =
    state === 'running' ||
    state === 'idle' ||
    state === 'terminating' ||
    state === 'terminated' ||
    state === 'archived'
      ? startedAt
      : undefined;
  const terminatedAt =
    state === 'terminated' || state === 'archived' || state === 'failed'
      ? session.archivedAt?.toISOString()
      : undefined;

  return {
    id: session.id,
    ravnId: toSessionRavnId(session),
    name: session.name,
    title: session.trackerIssue?.title ?? session.name,
    personaName: session.name,
    templateId: toSessionTemplateId(session),
    clusterId: toSessionClusterId(session),
    clusterName: session.instanceName ?? session.instanceId ?? undefined,
    state,
    startedAt,
    readyAt,
    lastActivityAt,
    terminatedAt,
    resources: EMPTY_SESSION_RESOURCES,
    env: {},
    events: [],
    bootProgress:
      state === 'provisioning' ? (session.status === 'starting' ? 0.25 : 0.6) : undefined,
    connectionType: 'cli',
    tokensIn: session.tokensUsed,
    tokensOut: 0,
    preview: toSessionPreview(session),
    trackerIssue: session.trackerIssue,
    origin: session.origin,
  };
}

function buildSplitVolundrService(
  catalog: IVolundrService,
  forge: IVolundrService,
): IVolundrService {
  return {
    ...catalog,
    getFeatures: () => forge.getFeatures(),
    getSessions: () => forge.getSessions(),
    getSession: (id) => forge.getSession(id),
    getActiveSessions: () => forge.getActiveSessions(),
    getStats: () => forge.getStats(),
    getRepos: () => forge.getRepos(),
    getTargets: () => Promise.resolve(forge.getTargets?.() ?? []),
    subscribe: (callback) => forge.subscribe(callback),
    subscribeStats: (callback) => forge.subscribeStats(callback),
    getAvailableMcpServers: () => forge.getAvailableMcpServers(),
    getAvailableSecrets: () => forge.getAvailableSecrets(),
    createSecret: (name, data) => forge.createSecret(name, data),
    getClusterResources: () => forge.getClusterResources(),
    getAdminSettings: () => forge.getAdminSettings(),
    updateAdminSettings: (data) => forge.updateAdminSettings(data),
    startSession: (config) => forge.startSession(config),
    evaluatePermissionAutoApproval: (sessionId, request) =>
      forge.evaluatePermissionAutoApproval(sessionId, request),
    connectSession: (config) => forge.connectSession(config),
    updateSession: (sessionId, updates) => forge.updateSession(sessionId, updates),
    stopSession: (sessionId) => forge.stopSession(sessionId),
    resumeSession: (sessionId) => forge.resumeSession(sessionId),
    deleteSession: (sessionId, cleanup) => forge.deleteSession(sessionId, cleanup),
    archiveSession: (sessionId) => forge.archiveSession(sessionId),
    archiveStoppedSessions: () => forge.archiveStoppedSessions(),
    restoreSession: (sessionId) => forge.restoreSession(sessionId),
    listArchivedSessions: () => forge.listArchivedSessions(),
    listExternalSessions: () => forge.listExternalSessions(),
    importExternalSession: (provider, externalId, name) =>
      forge.importExternalSession(provider, externalId, name),
    getMessages: (sessionId) => forge.getMessages(sessionId),
    sendMessage: (sessionId, content) => forge.sendMessage(sessionId, content),
    subscribeMessages: (sessionId, callback) => forge.subscribeMessages(sessionId, callback),
    getLogs: (sessionId, limit) => forge.getLogs(sessionId, limit),
    subscribeLogs: (sessionId, callback) => forge.subscribeLogs(sessionId, callback),
    getAggregatedLogs: (sessionId, options) => forge.getAggregatedLogs(sessionId, options),
    subscribeAggregatedLogs: (sessionId, options, callback) =>
      forge.subscribeAggregatedLogs(sessionId, options, callback),
    getChronicle: (sessionId) => forge.getChronicle(sessionId),
    subscribeChronicle: (sessionId, callback) => forge.subscribeChronicle(sessionId, callback),
  };
}

function applySessionFilters(sessions: Session[], filters?: SessionFilters): Session[] {
  if (!filters) return sessions;
  return sessions.filter((session) => {
    if (filters.state && session.state !== filters.state) return false;
    if (filters.clusterId && session.clusterId !== filters.clusterId) return false;
    if (filters.ravnId && session.ravnId !== filters.ravnId) return false;
    return true;
  });
}

async function listAllVolundrSessions(volundr: IVolundrService): Promise<Session[]> {
  const [sessions, archived] = await Promise.all([
    volundr.getSessions(),
    volundr.listArchivedSessions().catch(() => []),
  ]);
  const byId = new Map<string, Session>();
  for (const session of [...sessions, ...archived]) {
    byId.set(session.id, toDomainSession(session));
  }
  return Array.from(byId.values());
}

function buildVolundrSessionStore(volundr: IVolundrService): ISessionStore {
  return {
    async getSession(id: string) {
      const session = await volundr.getSession(id);
      if (session) return toDomainSession(session);
      const archived = await volundr.listArchivedSessions().catch(() => []);
      const archivedSession = archived.find((candidate: VolundrSession) => candidate.id === id);
      return archivedSession ? toDomainSession(archivedSession) : null;
    },
    async listSessions(filters?: SessionFilters) {
      return applySessionFilters(await listAllVolundrSessions(volundr), filters);
    },
    async createSession() {
      throw new UnsupportedSessionStoreOperationError('create');
    },
    async updateSession() {
      throw new UnsupportedSessionStoreOperationError('update');
    },
    async deleteSession(id: string) {
      await volundr.deleteSession(id);
    },
    subscribe(callback: (sessions: Session[]) => void) {
      let active = true;
      const emit = async (sessions?: VolundrSession[]) => {
        const current = sessions?.map(toDomainSession) ?? [];
        const archived = await volundr.listArchivedSessions().catch(() => []);
        if (!active) return;
        const byId = new Map<string, Session>();
        for (const session of [...current, ...archived.map(toDomainSession)]) {
          byId.set(session.id, session);
        }
        callback(Array.from(byId.values()));
      };
      void emit();
      const unsubscribe = volundr.subscribe((sessions: VolundrSession[]) => {
        void emit(sessions);
      });
      return () => {
        active = false;
        unsubscribe();
      };
    },
  };
}

function parseMemoryToMi(value: unknown, fallback: number): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value !== 'string') return fallback;
  const trimmed = value.trim();
  const match = trimmed.match(/^(\d+(?:\.\d+)?)([KMGTP]i?)?$/i);
  if (!match) return fallback;
  const amount = Number(match[1]);
  const unit = (match[2] ?? 'Mi').toLowerCase();
  switch (unit) {
    case 'ki':
      return Math.round(amount / 1024);
    case 'mi':
      return Math.round(amount);
    case 'gi':
      return Math.round(amount * 1024);
    case 'ti':
      return Math.round(amount * 1024 * 1024);
    default:
      return fallback;
  }
}

function parseCpuCores(value: unknown, fallback: number): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value !== 'string') return fallback;
  const trimmed = value.trim();
  if (trimmed.endsWith('m')) {
    const milli = Number(trimmed.slice(0, -1));
    return Number.isFinite(milli) ? milli / 1000 : fallback;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseIntegerResource(value: unknown, fallback: number): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value !== 'string') return fallback;
  const parsed = Number(value.trim());
  return Number.isFinite(parsed) ? parsed : fallback;
}

type ClusterResourceRecord = {
  instances?: Array<{
    id: string;
    name: string;
    slug: string;
    isDefault?: boolean;
    is_default?: boolean;
    tags?: string[];
  }>;
  resourceTypes?: Array<{ name?: string; resourceKey?: string }>;
  nodes?: Array<{
    name: string;
    instanceId?: string;
    instance_id?: string;
    instanceName?: string;
    instance_name?: string;
    instanceSlug?: string;
    instance_slug?: string;
    labels?: Record<string, string>;
    allocatable?: Record<string, string>;
    allocated?: Record<string, string>;
    available?: Record<string, string>;
  }>;
};

type VolundrTargetRecord = Awaited<ReturnType<IVolundrService['getTargets']>>[number];
type ClusterNodeRecord = NonNullable<ClusterResourceRecord['nodes']>[number] & {
  id: string;
  status: Cluster['nodes'][number]['status'];
  role: string;
  allocatable: Record<string, string>;
  allocated: Record<string, string>;
  available: Record<string, string>;
  labels: Record<string, string>;
};

function toPodStatus(session: VolundrSession): Cluster['pods'][number]['status'] {
  switch (session.status) {
    case 'created':
    case 'starting':
    case 'provisioning':
      return 'pending';
    case 'running':
      if (session.activityState === 'error') return 'failed';
      return session.activityState === 'idle' ? 'idle' : 'running';
    case 'failed':
    case 'error':
      return 'failed';
    default:
      return 'succeeded';
  }
}

function clusterKey(value: unknown): string {
  return String(value ?? '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function targetFromResourceInstance(
  instance: NonNullable<ClusterResourceRecord['instances']>[number],
): VolundrTargetRecord {
  return {
    id: instance.id,
    slug: instance.slug,
    name: instance.name,
    baseUrl: '',
    enabled: true,
    isDefault: instance.isDefault ?? instance.is_default ?? false,
    visibility: 'system',
    tags: instance.tags ?? [],
  };
}

function sessionBelongsToTarget(session: VolundrSession, target: VolundrTargetRecord): boolean {
  if (session.instanceId && session.instanceId === target.id) return true;
  const sessionName = clusterKey(session.instanceName);
  if (!sessionName) return false;
  return sessionName === clusterKey(target.name) || sessionName === clusterKey(target.slug);
}

function nodeBelongsToTarget(node: ClusterNodeRecord, target: VolundrTargetRecord): boolean {
  const ids = [
    node.instanceId,
    node.instance_id,
    node.instanceName,
    node.instance_name,
    node.instanceSlug,
    node.instance_slug,
  ].map(clusterKey);
  return (
    ids.includes(clusterKey(target.id)) ||
    ids.includes(clusterKey(target.name)) ||
    ids.includes(clusterKey(target.slug))
  );
}

function parseRealmFromTarget(target: VolundrTargetRecord): string {
  try {
    const host = new URL(target.baseUrl).hostname;
    const parts = host.split('.');
    const slugIndex = parts.findIndex((part) => clusterKey(part) === clusterKey(target.slug));
    if (slugIndex >= 0 && parts[slugIndex + 1]) return parts[slugIndex + 1]!;
  } catch {
    // Keep the display stable when baseUrl is absent or not absolute.
  }
  return target.tags.find((tag) => !['cpu', 'gpu'].includes(clusterKey(tag))) ?? target.slug;
}

function capacityFromNodes(nodes: ClusterNodeRecord[]): Cluster['capacity'] {
  return nodes.reduce(
    (acc, node) => ({
      cpu: acc.cpu + parseCpuCores(node.allocatable.cpu, 0),
      memMi: acc.memMi + parseMemoryToMi(node.allocatable.memory, 0),
      gpu: acc.gpu + parseIntegerResource(node.allocatable['nvidia.com/gpu'], 0),
    }),
    { cpu: 0, memMi: 0, gpu: 0 },
  );
}

function usedFromNodes(nodes: ClusterNodeRecord[]): Cluster['used'] {
  return nodes.reduce(
    (acc, node) => ({
      cpu: acc.cpu + parseCpuCores(node.allocated.cpu, 0),
      memMi: acc.memMi + parseMemoryToMi(node.allocated.memory, 0),
      gpu: acc.gpu + parseIntegerResource(node.allocated['nvidia.com/gpu'], 0),
    }),
    { cpu: 0, memMi: 0, gpu: 0 },
  );
}

function buildClusterFromParts(
  target: VolundrTargetRecord,
  nodes: ClusterNodeRecord[],
  sessions: VolundrSession[],
): Cluster {
  const capacity = capacityFromNodes(nodes);
  const used = usedFromNodes(nodes);
  const sampleLabels = nodes[0]?.labels ?? {};
  const region =
    sampleLabels['topology.kubernetes.io/region'] ??
    sampleLabels['failure-domain.beta.kubernetes.io/region'] ??
    target.slug;
  const isGpu = capacity.gpu > 0 || target.tags.some((tag) => clusterKey(tag) === 'gpu');

  return {
    id: target.id,
    realm: parseRealmFromTarget(target),
    name: target.name,
    kind: isGpu ? 'gpu' : 'primary',
    status: nodes.length > 0 || sessions.length > 0 ? 'healthy' : 'warning',
    region,
    capacity,
    used,
    disk: {
      usedGi: 0,
      totalGi: 0,
      systemGi: 0,
      podsGi: 0,
      logsGi: 0,
    },
    nodes: nodes.map((node) => ({
      id: node.id,
      status: node.status,
      role: node.role,
    })),
    pods: sessions.map((session) => ({
      name: session.podName ?? session.name,
      status: toPodStatus(session),
      startedAt: toIsoFromEpochMs(session.lastActive) ?? new Date(0).toISOString(),
      cpuUsed: 0,
      cpuLimit: 0,
      memUsedMi: 0,
      memLimitMi: 0,
      restarts: 0,
    })),
    runningSessions: sessions.filter((session) => session.status === 'running').length,
    queuedProvisions: sessions.filter(
      (session) =>
        session.status === 'created' ||
        session.status === 'starting' ||
        session.status === 'provisioning',
    ).length,
  };
}

function buildVolundrClusterAdapter(volundr: IVolundrService): IClusterAdapter {
  return {
    async getClusters() {
      const [resources, sessions, rawTargets] = await Promise.all([
        volundr
          .getClusterResources()
          .catch(() => ({ resourceTypes: [], nodes: [] }) as ClusterResourceRecord),
        volundr.getSessions().catch(() => [] as VolundrSession[]),
        Promise.resolve(volundr.getTargets?.() ?? []).catch(() => [] as VolundrTargetRecord[]),
      ]);

      const nodes = (resources.nodes ?? []).map((node, index) => ({
        id: node.name || `node-${index + 1}`,
        name: node.name || `node-${index + 1}`,
        status: 'ready' as const,
        role:
          node.labels?.['node-role.kubernetes.io/control-plane'] != null ||
          node.labels?.['node-role.kubernetes.io/master'] != null
            ? 'control-plane'
            : 'worker',
        allocatable: node.allocatable ?? {},
        allocated: node.allocated ?? {},
        available: node.available ?? {},
        labels: node.labels ?? {},
        instanceId: node.instanceId,
        instance_id: node.instance_id,
        instanceName: node.instanceName,
        instance_name: node.instance_name,
        instanceSlug: node.instanceSlug,
        instance_slug: node.instance_slug,
      }));
      const targets =
        rawTargets.length > 0
          ? rawTargets
          : (resources.instances ?? []).map(targetFromResourceInstance);

      if (targets.length > 0) {
        const claimedSessionIds = new Set<string>();
        const clusters = targets.map((target) => {
          const targetSessions = sessions.filter((session) => {
            const belongs = sessionBelongsToTarget(session, target);
            if (belongs) claimedSessionIds.add(session.id);
            return belongs;
          });
          return buildClusterFromParts(
            target,
            nodes.filter((node) => nodeBelongsToTarget(node, target)),
            targetSessions,
          );
        });

        const unclaimedSessions = sessions.filter((session) => !claimedSessionIds.has(session.id));
        if (unclaimedSessions.length > 0) {
          clusters.push(
            buildClusterFromParts(
              {
                id: 'shared',
                slug: 'shared',
                name: 'Shared Forge',
                baseUrl: '',
                enabled: true,
                isDefault: false,
                visibility: 'system',
                tags: [],
              },
              nodes.filter((node) => !targets.some((target) => nodeBelongsToTarget(node, target))),
              unclaimedSessions,
            ),
          );
        }

        return clusters;
      }

      if (nodes.length === 0 && sessions.length === 0) return [];

      return [
        buildClusterFromParts(
          {
            id: 'shared',
            slug: 'shared',
            name: capacityFromNodes(nodes).gpu > 0 ? 'Shared GPU Forge' : 'Shared Forge',
            baseUrl: '',
            enabled: true,
            isDefault: false,
            visibility: 'system',
            tags: [],
          },
          nodes,
          sessions,
        ),
      ];
    },
    async getCluster(id: string) {
      const clusters = await this.getClusters();
      return clusters.find((cluster) => cluster.id === id) ?? null;
    },
  };
}

export function buildServices(config: NiuuConfig): ServicesMap {
  const mimirSvc = config.services['mimir'];

  // ── Ravn: all five sub-services share one HTTP base URL when configured ──
  const ravnPersonaBase = resolveRavnServiceBase(config, 'ravn.personas');
  const ravnRavenBase = resolveRavnServiceBase(config, 'ravn.ravens');
  const ravnSessionBase = resolveRavnServiceBase(config, 'ravn.sessions');
  const ravnTriggerBase = resolveRavnServiceBase(config, 'ravn.triggers');
  const ravnBudgetBase = resolveRavnServiceBase(config, 'ravn.budget');
  const ravnWardenBase = resolveRavnServiceBase(config, 'ravn.wardens');
  const ravnPersonas = ravnPersonaBase
    ? buildRavnPersonaAdapter(createApiClient(ravnPersonaBase))
    : demoService(config, 'ravn.personas', createMockPersonaStore);
  const ravnRavens = ravnRavenBase
    ? buildRavnRavenAdapter(createApiClient(ravnRavenBase))
    : demoService(config, 'ravn.ravens', createMockRavenStream);
  const ravnResidents: IResidentControl = ravnRavenBase
    ? buildRavnResidentControlAdapter(createApiClient(ravnRavenBase))
    : unavailableService<IResidentControl>('ravn.residents');
  const ravnSessions = ravnSessionBase
    ? buildRavnSessionAdapter(createApiClient(ravnSessionBase))
    : demoService(config, 'ravn.sessions', createMockSessionStream);
  const ravnTriggers = ravnTriggerBase
    ? buildRavnTriggerAdapter(createApiClient(ravnTriggerBase))
    : demoService(config, 'ravn.triggers', createMockTriggerStore);
  const ravnBudget = ravnBudgetBase
    ? buildRavnBudgetAdapter(createApiClient(ravnBudgetBase))
    : demoService(config, 'ravn.budget', createMockBudgetStream);
  const ravnWardens = ravnWardenBase
    ? buildRavnWardenAdapter(createApiClient(ravnWardenBase))
    : demoService(config, 'ravn.wardens', createMockWardenStore);

  // ── Mímir ──
  const mimir = hasHttpBackend(mimirSvc)
    ? buildMimirHttpAdapter(createApiClient(mimirSvc.baseUrl))
    : demoService(config, 'mimir', createMimirMockAdapter);
  const bifrostBase = resolveBifrostServiceBase(config);
  const bifrost: IBifrostService = bifrostBase
    ? buildBifrostHttpAdapter(createApiClient(bifrostBase))
    : demoService(config, 'bifrost', createMockBifrostService);

  // ── Völundr catalog + Forge runtime ──
  const forgeBase = resolveForgeServiceBase(config);
  const volundrBase = resolveVolundrServiceBase(config);
  const forgeVolundr = forgeBase
    ? buildVolundrHttpAdapter(createApiClient(forgeBase), undefined, {
        niuuBasePath: resolveNiuuRegistryBase(config),
      })
    : demoService(config, 'forge', createMockVolundrService);
  const catalogVolundr = volundrBase
    ? buildVolundrHttpAdapter(createApiClient(volundrBase), undefined, {
        niuuBasePath: resolveNiuuRegistryBase(config),
      })
    : demoService(config, 'volundr', createMockVolundrService);
  const volundr = buildSplitVolundrService(catalogVolundr, forgeVolundr);
  const repoCatalogBase = resolveRepoCatalogBase(config);
  const repoCatalogService = repoCatalogBase
    ? buildRepoCatalogHttpAdapter(createApiClient(repoCatalogBase))
    : demoService(config, 'niuu.repos', createMockRepoCatalogService);
  const sessionStore = forgeBase
    ? buildVolundrSessionStore(volundr)
    : demoService(config, 'volundr.sessions', createMockSessionStore);
  const clusterAdapter = forgeBase
    ? buildVolundrClusterAdapter(volundr)
    : demoService(config, 'volundr.clusters', createMockClusterAdapter);
  // ── Völundr streams: keyed as separate services so they can be flipped
  //    independently (e.g. mock PTY with live metrics during bring-up). ──
  const forgePtyWsUrl = resolveForgeStreamWsUrl(config);
  const forgeMetricsBase = resolveForgeMetricsBase(config);
  const filesystemBase = resolveFilesystemBase(config);
  const ptyStream = forgePtyWsUrl
    ? buildVolundrPtyWsAdapter({ urlTemplate: forgePtyWsUrl })
    : demoService(config, 'forge.pty', createMockPtyStream);
  const metricsStream = forgeMetricsBase
    ? buildVolundrMetricsSseAdapter({ urlTemplate: forgeMetricsBase })
    : demoService(config, 'forge.metrics', createMockMetricsStream);
  const filesystem = filesystemBase
    ? buildVolundrFileSystemHttpAdapter({ baseUrl: filesystemBase })
    : demoService(config, 'filesystem', createMockFileSystemPort);

  // ── Observatory ──
  const observatoryRegistryBase = resolveObservatoryServiceBase(config, 'observatory.registry');
  const observatoryTopologyBase = resolveObservatoryServiceBase(config, 'observatory.topology');
  const observatoryEventsBase = resolveObservatoryServiceBase(config, 'observatory.events');
  const observatoryAgentsBase = resolveObservatoryServiceBase(config, 'observatory.agents');
  const observatoryRegistry = observatoryRegistryBase
    ? buildObservatoryRegistryHttpAdapter(createApiClient(observatoryRegistryBase))
    : demoService(config, 'observatory.registry', createMockRegistryRepository);
  const observatoryTopology = observatoryTopologyBase
    ? buildObservatoryTopologyAggregateAdapter(createApiClient(observatoryTopologyBase))
    : demoService(config, 'observatory.topology', createMockTopologyStream);
  const observatoryEvents = observatoryEventsBase
    ? buildObservatoryEventsSseStream(observatoryEventsBase)
    : demoService(config, 'observatory.events', createMockEventStream);
  const observatoryAgents = observatoryAgentsBase
    ? buildObservatoryAgentDirectoryHttpAdapter(createApiClient(observatoryAgentsBase))
    : demoService(config, 'observatory.agents', createMockAgentDirectory);
  // ── Valkyrie ──
  const valkyrieBase = resolveValkyrieServiceBase(config, 'valkyrie');
  const valkyrieReviewsBase = resolveValkyrieServiceBase(config, 'valkyrie.reviews');
  const valkyrie = valkyrieBase
    ? buildValkyrieHttpAdapter(createApiClient(valkyrieBase))
    : demoService(config, 'valkyrie', createMockValkyrieService);
  const valkyrieReviews = valkyrieReviewsBase
    ? buildOdinReviewHttpAdapter(createApiClient(valkyrieReviewsBase))
    : demoService(config, 'valkyrie.reviews', createMockOdinReviewService);
  const valkyrieSkillsBase = resolveValkyrieServiceBase(config, 'valkyrie.skills');
  const valkyrieSkills = valkyrieSkillsBase
    ? buildValkyrieSkillsHttpAdapter(createApiClient(valkyrieSkillsBase))
    : demoService(config, 'valkyrie.skills', createMockValkyrieSkillsService);
  const realmGovernanceBases = resolveRealmGovernanceBases(config);
  const valkyrieRealms = realmGovernanceBases
    ? buildRealmGovernanceHttpAdapter(
        createApiClient(realmGovernanceBases.realmsBase),
        createApiClient(realmGovernanceBases.workflowsBase),
      )
    : demoService(config, 'valkyrie.realms', createMockRealmGovernanceService);
  const featureCatalogService = buildSharedFeatureCatalogService(config);
  const identityService = buildSharedIdentityService(config);

  // ── Ting ──
  const tingBase = resolveTingServiceBase(config, 'ting');
  const tingClient = tingBase ? createApiClient(tingBase) : null;
  const dispatcherBase = resolveTingServiceBase(config, 'ting.dispatcher');
  const dispatcherClient = dispatcherBase ? createApiClient(dispatcherBase) : null;
  const tingSessionsBase = resolveTingServiceBase(config, 'ting.sessions');
  const tingSessionsClient = tingSessionsBase ? createApiClient(tingSessionsBase) : null;
  const dispatchBase = resolveTingServiceBase(config, 'ting.dispatch');
  const dispatchClient = dispatchBase ? createApiClient(dispatchBase) : null;
  const tingSettingsBase = resolveTingServiceBase(config, 'ting.settings');
  const tingSettingsClient = tingSettingsBase ? createApiClient(tingSettingsBase) : null;
  const trackerBase = resolveCanonicalServiceBase(config, 'tracker');
  const trackerClient = trackerBase ? createApiClient(trackerBase) : null;
  const auditBase = resolveCanonicalServiceBase(config, 'audit');
  const auditClient = auditBase ? createApiClient(auditBase) : null;
  const workflowBase = resolveTingServiceBase(config, 'ting.workflows');
  const workflowClient = workflowBase ? createApiClient(workflowBase) : null;
  const researchBase = resolveTingServiceBase(config, 'ting.research');
  const researchClient = researchBase ? createApiClient(researchBase) : null;
  const specsBase = resolveTingServiceBase(config, 'ting.specs');
  const specsClient = specsBase ? createApiClient(specsBase) : null;
  const tingService = tingClient
    ? buildTingHttpAdapter(tingClient)
    : demoService(config, 'ting', createMockTingService);
  const dispatcherService = dispatcherClient
    ? buildDispatcherHttpAdapter(dispatcherClient)
    : demoService(config, 'ting.dispatcher', createMockDispatcherService);
  const tingSessionService = tingSessionsClient
    ? buildTingSessionHttpAdapter(tingSessionsClient)
    : demoService(config, 'ting.sessions', createMockTingSessionService);
  const trackerService = trackerClient
    ? buildTrackerHttpAdapter(trackerClient)
    : demoService(config, 'ting.tracker', createMockTrackerService);
  const workflowService = workflowClient
    ? buildWorkflowHttpAdapter(workflowClient)
    : demoService(config, 'ting.workflows', createMockWorkflowService);
  const researchService = researchClient
    ? buildResearchHttpAdapter(researchClient)
    : demoService(config, 'ting.research', createMockResearchService);
  const specsService = specsClient
    ? buildSpecsHttpAdapter(specsClient)
    : demoService(config, 'ting.specs', createMockSpecsService);
  const dispatchBus = dispatchClient
    ? buildDispatchBusHttpAdapter(dispatchClient)
    : demoService(config, 'ting.dispatch', createMockDispatchBus);
  const tingSettingsService = tingSettingsClient
    ? buildTingSettingsHttpAdapter(tingSettingsClient)
    : demoService(config, 'ting.settings', createMockTingSettingsService);
  const tingAuditLogService = auditClient
    ? buildTingAuditLogHttpAdapter(auditClient)
    : demoService(config, 'ting.audit', createMockAuditLogService);

  return {
    ting: tingService,
    'ting.dispatcher': dispatcherService,
    'ting.sessions': tingSessionService,
    'ting.tracker': trackerService,
    'ting.workflows': workflowService,
    'ting.research': researchService,
    'ting.specs': specsService,
    'ting.dispatch': dispatchBus,
    'ting.settings': tingSettingsService,
    'ting.audit': tingAuditLogService,
    'ravn.personas': ravnPersonas,
    'ravn.ravens': ravnRavens,
    'ravn.residents': ravnResidents,
    'ravn.sessions': ravnSessions,
    'ravn.triggers': ravnTriggers,
    'ravn.budget': ravnBudget,
    'ravn.wardens': ravnWardens,
    mimir,
    bifrost,
    volundr,
    'niuu.repos': repoCatalogService,
    ptyStream,
    metricsStream,
    features: featureCatalogService,
    identity: identityService,
    filesystem,
    // NIU-678 pages (ClustersPage, HistoryPage)
    'volundr.clusters': clusterAdapter,
    'volundr.sessions': sessionStore,
    // VolundrPage overview hooks (useVolundrClusters, useSessionStore)
    clusterAdapter,
    sessionStore,
    'observatory.registry': observatoryRegistry,
    'observatory.topology': observatoryTopology,
    'observatory.events': observatoryEvents,
    'observatory.agents': observatoryAgents,
    valkyrie,
    'valkyrie.reviews': valkyrieReviews,
    'valkyrie.skills': valkyrieSkills,
    'valkyrie.realms': valkyrieRealms,
  };
}
