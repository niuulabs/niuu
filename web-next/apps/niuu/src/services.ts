import {
  createMockPersonaStore,
  createMockRavenStream,
  createMockSessionStream,
  createMockTriggerStore,
  createMockBudgetStream,
  buildRavnPersonaAdapter,
  buildRavnRavenAdapter,
  buildRavnSessionAdapter,
  buildRavnTriggerAdapter,
  buildRavnBudgetAdapter,
} from '@niuulabs/plugin-ravn';
import {
  createMockTyrService,
  createMockDispatcherService,
  createMockTyrSessionService,
  createMockTrackerService,
  createMockWorkflowService,
  createMockDispatchBus,
  createMockTyrSettingsService,
  createMockAuditLogService,
  buildTyrHttpAdapter,
  buildDispatcherHttpAdapter,
  buildTyrSessionHttpAdapter,
  buildTrackerHttpAdapter,
  buildWorkflowHttpAdapter,
  buildDispatchBusHttpAdapter,
  buildTyrSettingsHttpAdapter,
  buildTyrAuditLogHttpAdapter,
} from '@niuulabs/plugin-tyr';
import { createMimirMockAdapter, buildMimirHttpAdapter } from '@niuulabs/plugin-mimir';
import {
  createMockRegistryRepository,
  createMockTopologyStream,
  createMockEventStream,
  buildObservatoryRegistryHttpAdapter,
  buildObservatoryTopologySseStream,
  buildObservatoryEventsSseStream,
} from '@niuulabs/plugin-observatory';
import {
  createMockVolundrService,
  createMockClusterAdapter,
  createMockTemplateStore,
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
  type ITemplateStore,
  type Session,
  type SessionFilters,
  type Template,
  type VolundrSession,
  type VolundrTemplate,
} from '@niuulabs/plugin-volundr';
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
  mode: 'live' | 'mock';
  transport: 'http' | 'ws' | 'mock';
  target: string | null;
  source: string;
  note?: string;
}

/**
 * A service config is "live" (i.e. should use a real transport) when its mode
 * is `http` or `ws` and a URL is present. Any other combination — missing
 * mode, `mock`, or missing URL — falls back to the mock adapter.
 */
function hasHttpBackend(
  svc: ServiceConfig | undefined,
): svc is ServiceConfig & { baseUrl: string } {
  return svc?.mode === 'http' && typeof svc.baseUrl === 'string';
}

function hasWsBackend(svc: ServiceConfig | undefined): svc is ServiceConfig & { wsUrl: string } {
  return svc?.mode === 'ws' && typeof svc.wsUrl === 'string';
}

function resolveDirectServiceWsUrl(
  config: Pick<NiuuConfig, 'services'>,
  ...serviceKeys: string[]
): string | null {
  for (const serviceKey of serviceKeys) {
    const svc = config.services[serviceKey];
    if (hasWsBackend(svc)) return svc.wsUrl;
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
        target: svc.wsUrl,
        source: serviceKey,
      };
    }
  }
  return {
    mode: 'mock',
    transport: 'mock',
    target: null,
    source: 'mock',
  };
}

export function toSharedApiBase(baseUrl: string): string {
  return baseUrl.replace(/\/(?:tyr|forge|volundr)\/?$/, '');
}

export function toHostBase(baseUrl: string): string {
  return baseUrl.replace(/\/api\/v1\/(?:forge|volundr)\/?$/, '');
}

export function toHostPtyWsUrl(baseUrl: string): string {
  const hostBase = toHostBase(baseUrl)
    .replace(/^http:/, 'ws:')
    .replace(/^https:/, 'wss:');
  return `${hostBase}/s/{sessionId}/session`;
}

export function resolveSharedApiBase(config: Pick<NiuuConfig, 'services'>): string | null {
  const tyrSvc = config.services['tyr'];
  if (hasHttpBackend(tyrSvc)) return toSharedApiBase(tyrSvc.baseUrl);

  const forgeSvc = config.services['forge'];
  if (hasHttpBackend(forgeSvc)) return toSharedApiBase(forgeSvc.baseUrl);

  const volundrSvc = config.services['volundr'];
  if (hasHttpBackend(volundrSvc)) return toSharedApiBase(volundrSvc.baseUrl);

  return null;
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
  return resolveDirectServiceBase(config, 'forge', 'volundr');
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
      mode: 'mock',
      transport: 'mock',
      target: null,
      source: 'mock',
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
  return resolveDirectServiceBase(config, 'volundr', 'forge');
}

function resolveForgeStreamWsUrl(config: Pick<NiuuConfig, 'services'>): string | null {
  const explicitWsUrl = resolveDirectServiceWsUrl(config, 'forge.pty', 'volundr.pty');
  if (explicitWsUrl) return explicitWsUrl;

  const forgeBase = resolveForgeServiceBase(config);
  return forgeBase ? toHostPtyWsUrl(forgeBase) : null;
}

function resolveForgeMetricsBase(config: Pick<NiuuConfig, 'services'>): string | null {
  return resolveDirectServiceBase(config, 'forge.metrics', 'volundr.metrics');
}

function resolveFilesystemBase(config: Pick<NiuuConfig, 'services'>): string | null {
  const explicitBase = resolveDirectServiceBase(config, 'filesystem');
  if (explicitBase) return explicitBase;

  const forgeBase = resolveForgeServiceBase(config);
  return forgeBase ? toHostBase(forgeBase) : null;
}

function resolveTyrServiceBase(
  config: Pick<NiuuConfig, 'services'>,
  serviceKey:
    | 'tyr'
    | 'tyr.dispatcher'
    | 'tyr.sessions'
    | 'tyr.dispatch'
    | 'tyr.settings'
    | 'tyr.workflows',
): string | null {
  const explicitBase = resolveDirectServiceBase(config, serviceKey);
  if (!explicitBase) return resolveDirectServiceBase(config, 'tyr');

  switch (serviceKey) {
    case 'tyr.dispatcher':
      return explicitBase.replace(/\/dispatcher\/?$/, '');
    case 'tyr.sessions':
      return explicitBase.replace(/\/sessions\/?$/, '');
    case 'tyr.dispatch':
      return explicitBase.replace(/\/dispatch\/?$/, '');
    case 'tyr.settings':
      return explicitBase.replace(/\/settings\/?$/, '');
    case 'tyr.workflows':
      return explicitBase.replace(/\/workflows\/?$/, '');
    default:
      return explicitBase;
  }
}

function resolveObservatoryServiceBase(
  config: Pick<NiuuConfig, 'services'>,
  serviceKey: 'observatory.registry' | 'observatory.topology' | 'observatory.events',
): string | null {
  const explicitBase = resolveDirectServiceBase(config, serviceKey);
  if (explicitBase) {
    if (serviceKey === 'observatory.registry') {
      return explicitBase.replace(/\/registry\/?$/, '');
    }
    return explicitBase;
  }

  const groupedBase = resolveDirectServiceBase(config, 'observatory');
  if (!groupedBase) return null;

  if (serviceKey === 'observatory.registry') return groupedBase;
  if (serviceKey === 'observatory.topology') return `${groupedBase}/topology`;
  return `${groupedBase}/events`;
}

export function resolveSettingsServiceBase(
  config: Pick<NiuuConfig, 'services'>,
  providerId: 'identity' | 'tyr' | 'volundr' | 'mimir' | 'ravn' | 'observatory',
): string | null {
  switch (providerId) {
    case 'identity':
      return resolveCanonicalServiceBase(config, 'identity');
    case 'tyr':
      return resolveTyrServiceBase(config, 'tyr.settings');
    case 'volundr':
      return resolveVolundrServiceBase(config);
    case 'mimir':
      return resolveDirectServiceBase(config, 'mimir');
    case 'ravn':
      return resolveDirectServiceBase(config, 'ravn');
    case 'observatory':
      return resolveDirectServiceBase(config, 'observatory');
    default:
      return null;
  }
}

function resolveRavnServiceBase(
  config: Pick<NiuuConfig, 'services'>,
  serviceKey: 'ravn.personas' | 'ravn.ravens' | 'ravn.sessions' | 'ravn.triggers' | 'ravn.budget',
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
    default:
      return explicitBase;
  }
}

function resolveRavnServiceStatus(
  config: Pick<NiuuConfig, 'services'>,
  serviceKey: 'ravn.personas' | 'ravn.ravens' | 'ravn.sessions' | 'ravn.triggers' | 'ravn.budget',
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
  if (!featuresBase) return createMockFeatureCatalogService();
  return buildFeatureCatalogAdapter(createApiClient(featuresBase));
}

export function buildSharedIdentityService(config: Pick<NiuuConfig, 'services'>): IIdentityService {
  const identityBase = resolveCanonicalServiceBase(config, 'identity');
  if (!identityBase) return createMockIdentityService();
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
  serviceKey: 'observatory.registry' | 'observatory.topology' | 'observatory.events',
): ServiceBackendStatus {
  const explicit = resolveDirectServiceStatus(config, 'http', serviceKey);
  if (explicit.mode === 'live') {
    if (serviceKey === 'observatory.registry' && explicit.target) {
      return { ...explicit, target: explicit.target.replace(/\/registry\/?$/, '') };
    }
    return explicit;
  }

  const grouped = resolveDirectServiceStatus(config, 'http', 'observatory');
  if (grouped.mode !== 'live' || !grouped.target) return grouped;

  if (serviceKey === 'observatory.registry') {
    return { ...grouped, source: 'observatory' };
  }
  if (serviceKey === 'observatory.topology') {
    return { ...grouped, target: `${grouped.target}/topology`, source: 'observatory' };
  }
  return { ...grouped, target: `${grouped.target}/events`, source: 'observatory' };
}

export function buildServiceBackendStatus(
  config: Pick<NiuuConfig, 'services'>,
): Record<string, ServiceBackendStatus> {
  const forgePtyStatus = resolveDirectServiceStatus(config, 'ws', 'forge.pty', 'volundr.pty');
  const derivedForgeBase = resolveForgeServiceBase(config);

  return {
    identity: resolveCanonicalServiceStatus(config, 'identity'),
    features: resolveCanonicalServiceStatus(config, 'features'),
    tracker: resolveCanonicalServiceStatus(config, 'tracker'),
    audit: resolveCanonicalServiceStatus(config, 'audit'),
    mimir: resolveDirectServiceStatus(config, 'http', 'mimir'),
    'observatory.registry': resolveObservatoryServiceStatus(config, 'observatory.registry'),
    'observatory.topology': resolveObservatoryServiceStatus(config, 'observatory.topology'),
    'observatory.events': resolveObservatoryServiceStatus(config, 'observatory.events'),
    'ravn.personas': resolveRavnServiceStatus(config, 'ravn.personas'),
    'ravn.ravens': resolveRavnServiceStatus(config, 'ravn.ravens'),
    'ravn.sessions': resolveRavnServiceStatus(config, 'ravn.sessions'),
    'ravn.triggers': resolveRavnServiceStatus(config, 'ravn.triggers'),
    'ravn.budget': resolveRavnServiceStatus(config, 'ravn.budget'),
    'niuu.repos': resolveRepoCatalogStatus(config),
    forge: resolveDirectServiceStatus(config, 'http', 'forge', 'volundr'),
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
    'forge.metrics': resolveDirectServiceStatus(config, 'http', 'forge.metrics', 'volundr.metrics'),
    tyr: resolveDirectServiceStatus(config, 'http', 'tyr'),
    'tyr.dispatcher': resolveDirectServiceStatus(config, 'http', 'tyr.dispatcher', 'tyr'),
    'tyr.sessions': resolveDirectServiceStatus(config, 'http', 'tyr.sessions', 'tyr'),
    'tyr.dispatch': resolveDirectServiceStatus(config, 'http', 'tyr.dispatch', 'tyr'),
    'tyr.settings': resolveDirectServiceStatus(config, 'http', 'tyr.settings', 'tyr'),
    'tyr.tracker': resolveCanonicalServiceStatus(config, 'tracker'),
    'tyr.audit': resolveCanonicalServiceStatus(config, 'audit'),
    'tyr.workflows': resolveDirectServiceStatus(config, 'http', 'tyr.workflows', 'tyr'),
    filesystem: (() => {
      const explicit = resolveDirectServiceStatus(config, 'http', 'filesystem');
      if (explicit.mode === 'live') return explicit;
      const derivedBase = resolveFilesystemBase(config);
      if (derivedBase) {
        return {
          mode: 'live',
          transport: 'http',
          target: derivedBase,
          source: 'forge-host',
        } satisfies ServiceBackendStatus;
      }
      return {
        mode: 'mock',
        transport: 'mock',
        target: null,
        source: 'mock',
        note: 'No live filesystem API is wired yet.',
      } satisfies ServiceBackendStatus;
    })(),
  };
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
      return session.activityState === 'idle' ? 'idle' : 'running';
    case 'stopping':
      return 'terminating';
    case 'stopped':
    case 'archived':
      return 'terminated';
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
  return session.podName ?? session.hostname ?? session.tenantId ?? 'shared';
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
    state === 'running' || state === 'idle' || state === 'terminating' || state === 'terminated'
      ? startedAt
      : undefined;
  const terminatedAt =
    state === 'terminated' || state === 'failed' ? session.archivedAt?.toISOString() : undefined;

  return {
    id: session.id,
    ravnId: toSessionRavnId(session),
    personaName: session.name,
    templateId: toSessionTemplateId(session),
    clusterId: toSessionClusterId(session),
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
      throw new Error(
        'Session creation is not yet supported through the app session-store adapter.',
      );
    },
    async updateSession() {
      throw new Error(
        'Session updates are not yet supported through the app session-store adapter.',
      );
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

type ForgeTemplateRecord = VolundrTemplate & {
  createdAt?: string;
  updatedAt?: string;
  env_vars?: Record<string, string>;
  env_secret_refs?: string[];
  resource_config?: Record<string, unknown>;
  mcp_servers?: Array<Record<string, unknown>>;
  workload_config?: Record<string, unknown>;
};

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

function toStringRecord(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object') return {};
  return Object.fromEntries(
    Object.entries(value).filter(
      (entry): entry is [string, string] => typeof entry[1] === 'string',
    ),
  );
}

function toStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

type ClusterResourceRecord = {
  resourceTypes?: Array<{ name?: string; resourceKey?: string }>;
  nodes?: Array<{
    name: string;
    labels?: Record<string, string>;
    allocatable?: Record<string, string>;
    allocated?: Record<string, string>;
    available?: Record<string, string>;
  }>;
};

function toTemplateSpec(raw: ForgeTemplateRecord): Template['spec'] {
  const workloadConfig =
    raw.workloadConfig && typeof raw.workloadConfig === 'object'
      ? raw.workloadConfig
      : raw.workload_config;
  const resourceConfig =
    raw.resourceConfig && typeof raw.resourceConfig === 'object'
      ? raw.resourceConfig
      : raw.resource_config;
  const env = toStringRecord(raw.envVars ?? raw.env_vars);
  const envSecretRefs = toStringArray(raw.envSecretRefs ?? raw.env_secret_refs);
  const tools = toStringArray((workloadConfig as Record<string, unknown> | undefined)?.tools);
  const ttlSec = Number((workloadConfig as Record<string, unknown> | undefined)?.ttlSec ?? 3600);
  const idleTimeoutSec = Number(
    (workloadConfig as Record<string, unknown> | undefined)?.idleTimeoutSec ?? 600,
  );
  const imageValue = (workloadConfig as Record<string, unknown> | undefined)?.image;
  const imageRef =
    typeof imageValue === 'string' && imageValue.length > 0
      ? imageValue
      : 'ghcr.io/niuulabs/skuld:latest';
  const imageTagIndex = imageRef.lastIndexOf(':');
  const image =
    imageTagIndex > imageRef.lastIndexOf('/') ? imageRef.slice(0, imageTagIndex) : imageRef;
  const tag =
    imageTagIndex > imageRef.lastIndexOf('/') ? imageRef.slice(imageTagIndex + 1) : 'latest';

  const mounts: Template['spec']['mounts'] = Array.isArray(raw.repos)
    ? raw.repos.reduce<Template['spec']['mounts']>((acc, repo, index) => {
        if (!repo || typeof repo !== 'object') return acc;
        const url = typeof repo.url === 'string' ? repo.url : null;
        if (!url) return acc;
        const branch = typeof repo.branch === 'string' ? repo.branch : 'main';
        const name =
          typeof repo.name === 'string'
            ? repo.name
            : typeof repo.repo === 'string'
              ? repo.repo
              : `repo-${index + 1}`;
        const mountPath =
          typeof repo.path === 'string'
            ? repo.path
            : `/workspace/${name.replace(/[^a-zA-Z0-9._-]+/g, '-')}`;
        acc.push({
          name,
          mountPath,
          source: { kind: 'git', repo: url, branch },
          readOnly: false,
        });
        return acc;
      }, [])
    : [];

  const resourceMap = (resourceConfig as Record<string, unknown> | undefined) ?? {};
  const mcpServers: NonNullable<Template['spec']['mcpServers']> = Array.isArray(
    raw.mcpServers ?? raw.mcp_servers,
  )
    ? (raw.mcpServers ?? raw.mcp_servers)!.reduce<NonNullable<Template['spec']['mcpServers']>>(
        (acc, server, index) => {
          const record =
            server && typeof server === 'object'
              ? (server as unknown as Record<string, unknown>)
              : {};
          const transport = typeof record.transport === 'string' ? record.transport : 'stdio';
          const connectionString =
            typeof record.connectionString === 'string'
              ? record.connectionString
              : typeof record.command === 'string'
                ? record.command
                : typeof record.url === 'string'
                  ? record.url
                  : '';
          acc.push({
            name: typeof record.name === 'string' ? record.name : `server-${index + 1}`,
            transport,
            connectionString,
            tools: toStringArray(record.tools),
          });
          return acc;
        },
        [],
      )
    : [];

  return {
    image,
    tag,
    mounts,
    env,
    envSecretRefs,
    tools,
    mcpServers,
    resources: {
      cpuRequest: String(
        resourceMap.cpuRequest ?? resourceMap.cpu_request ?? resourceMap.cpu ?? '1',
      ),
      cpuLimit: String(resourceMap.cpuLimit ?? resourceMap.cpu_limit ?? resourceMap.cpu ?? '2'),
      memRequestMi: parseMemoryToMi(
        resourceMap.memRequestMi ??
          resourceMap.memoryRequestMi ??
          resourceMap.memory_request ??
          resourceMap.memory,
        1024,
      ),
      memLimitMi: parseMemoryToMi(
        resourceMap.memLimitMi ??
          resourceMap.memoryLimitMi ??
          resourceMap.memory_limit ??
          resourceMap.memory,
        2048,
      ),
      gpuCount: Number(resourceMap.gpuCount ?? resourceMap.gpu ?? 0),
    },
    ttlSec: Number.isFinite(ttlSec) ? ttlSec : 3600,
    idleTimeoutSec: Number.isFinite(idleTimeoutSec) ? idleTimeoutSec : 600,
    clusterAffinity: toStringArray(
      (workloadConfig as Record<string, unknown> | undefined)?.clusterAffinity ??
        (workloadConfig as Record<string, unknown> | undefined)?.cluster_affinity,
    ),
    tolerations: toStringArray(
      (workloadConfig as Record<string, unknown> | undefined)?.tolerations,
    ),
  };
}

function toDomainTemplate(raw: ForgeTemplateRecord): Template {
  return {
    id: raw.name,
    name: raw.name,
    description: raw.description || undefined,
    version: 1,
    spec: toTemplateSpec(raw),
    createdAt: raw.createdAt ?? new Date(0).toISOString(),
    updatedAt: raw.updatedAt ?? new Date(0).toISOString(),
  };
}

function buildVolundrTemplateStore(volundr: IVolundrService): ITemplateStore {
  return {
    async getTemplate(id: string) {
      const template = (await volundr.getTemplate(id)) as ForgeTemplateRecord | null;
      return template ? toDomainTemplate(template) : null;
    },
    async listTemplates() {
      return ((await volundr.getTemplates()) as ForgeTemplateRecord[]).map(toDomainTemplate);
    },
    async createTemplate() {
      throw new Error(
        'Template creation is not yet supported through the live forge template adapter.',
      );
    },
    async updateTemplate() {
      throw new Error(
        'Template updates are not yet supported through the live forge template adapter.',
      );
    },
    async deleteTemplate() {
      throw new Error(
        'Template deletion is not yet supported through the live forge template adapter.',
      );
    },
  };
}

function toPodStatus(session: VolundrSession): Cluster['pods'][number]['status'] {
  switch (session.status) {
    case 'created':
    case 'starting':
    case 'provisioning':
      return 'pending';
    case 'running':
      return session.activityState === 'idle' ? 'idle' : 'running';
    case 'failed':
    case 'error':
      return 'failed';
    default:
      return 'succeeded';
  }
}

function buildVolundrClusterAdapter(volundr: IVolundrService): IClusterAdapter {
  return {
    async getClusters() {
      const [resources, sessions] = await Promise.all([
        volundr
          .getClusterResources()
          .catch(() => ({ resourceTypes: [], nodes: [] }) as ClusterResourceRecord),
        volundr.getSessions().catch(() => [] as VolundrSession[]),
      ]);

      const nodes = (resources.nodes ?? []).map((node, index) => ({
        id: node.name || `node-${index + 1}`,
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
      }));

      if (nodes.length === 0 && sessions.length === 0) return [];

      const capacity = nodes.reduce(
        (acc, node) => ({
          cpu: acc.cpu + parseCpuCores(node.allocatable.cpu, 0),
          memMi: acc.memMi + parseMemoryToMi(node.allocatable.memory, 0),
          gpu: acc.gpu + parseIntegerResource(node.allocatable['nvidia.com/gpu'], 0),
        }),
        { cpu: 0, memMi: 0, gpu: 0 },
      );
      const used = nodes.reduce(
        (acc, node) => ({
          cpu: acc.cpu + parseCpuCores(node.allocated.cpu, 0),
          memMi: acc.memMi + parseMemoryToMi(node.allocated.memory, 0),
          gpu: acc.gpu + parseIntegerResource(node.allocated['nvidia.com/gpu'], 0),
        }),
        { cpu: 0, memMi: 0, gpu: 0 },
      );

      const sampleLabels = nodes[0]?.labels ?? {};
      const region =
        sampleLabels['topology.kubernetes.io/region'] ??
        sampleLabels['failure-domain.beta.kubernetes.io/region'] ??
        'shared';
      const cluster: Cluster = {
        id: 'shared',
        realm: 'shared',
        name: capacity.gpu > 0 ? 'Shared GPU Forge' : 'Shared Forge',
        kind: capacity.gpu > 0 ? 'gpu' : 'primary',
        status: nodes.length > 0 ? 'healthy' : 'warning',
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

      return [cluster];
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
  const ravnPersonas = ravnPersonaBase
    ? buildRavnPersonaAdapter(createApiClient(ravnPersonaBase))
    : createMockPersonaStore();
  const ravnRavens = ravnRavenBase
    ? buildRavnRavenAdapter(createApiClient(ravnRavenBase))
    : createMockRavenStream();
  const ravnSessions = ravnSessionBase
    ? buildRavnSessionAdapter(createApiClient(ravnSessionBase))
    : createMockSessionStream();
  const ravnTriggers = ravnTriggerBase
    ? buildRavnTriggerAdapter(createApiClient(ravnTriggerBase))
    : createMockTriggerStore();
  const ravnBudget = ravnBudgetBase
    ? buildRavnBudgetAdapter(createApiClient(ravnBudgetBase))
    : createMockBudgetStream();

  // ── Mímir ──
  const mimir = hasHttpBackend(mimirSvc)
    ? buildMimirHttpAdapter(createApiClient(mimirSvc.baseUrl))
    : createMimirMockAdapter();

  // ── Völundr request/response ──
  const forgeBase = resolveForgeServiceBase(config);
  const volundrBase = resolveVolundrServiceBase(config);
  const volundr = volundrBase
    ? buildVolundrHttpAdapter(createApiClient(volundrBase))
    : createMockVolundrService();
  const repoCatalogBase = resolveRepoCatalogBase(config);
  const repoCatalogService = repoCatalogBase
    ? buildRepoCatalogHttpAdapter(createApiClient(repoCatalogBase))
    : createMockRepoCatalogService();
  const sessionStore = forgeBase ? buildVolundrSessionStore(volundr) : createMockSessionStore();
  const clusterAdapter = forgeBase
    ? buildVolundrClusterAdapter(volundr)
    : createMockClusterAdapter();
  const templateStore = forgeBase ? buildVolundrTemplateStore(volundr) : createMockTemplateStore();

  // ── Völundr streams: keyed as separate services so they can be flipped
  //    independently (e.g. mock PTY with live metrics during bring-up). ──
  const forgePtyWsUrl = resolveForgeStreamWsUrl(config);
  const forgeMetricsBase = resolveForgeMetricsBase(config);
  const filesystemBase = resolveFilesystemBase(config);
  const ptyStream = forgePtyWsUrl
    ? buildVolundrPtyWsAdapter({ urlTemplate: forgePtyWsUrl })
    : createMockPtyStream();
  const metricsStream = forgeMetricsBase
    ? buildVolundrMetricsSseAdapter({ urlTemplate: forgeMetricsBase })
    : createMockMetricsStream();
  const filesystem = filesystemBase
    ? buildVolundrFileSystemHttpAdapter({ baseUrl: filesystemBase })
    : createMockFileSystemPort();

  // ── Observatory ──
  const observatoryRegistryBase = resolveObservatoryServiceBase(config, 'observatory.registry');
  const observatoryTopologyBase = resolveObservatoryServiceBase(config, 'observatory.topology');
  const observatoryEventsBase = resolveObservatoryServiceBase(config, 'observatory.events');
  const observatoryRegistry = observatoryRegistryBase
    ? buildObservatoryRegistryHttpAdapter(createApiClient(observatoryRegistryBase))
    : createMockRegistryRepository();
  const observatoryTopology = observatoryTopologyBase
    ? buildObservatoryTopologySseStream(observatoryTopologyBase)
    : createMockTopologyStream();
  const observatoryEvents = observatoryEventsBase
    ? buildObservatoryEventsSseStream(observatoryEventsBase)
    : createMockEventStream();
  const featureCatalogService = buildSharedFeatureCatalogService(config);
  const identityService = buildSharedIdentityService(config);

  // ── Tyr ──
  const tyrBase = resolveTyrServiceBase(config, 'tyr');
  const tyrClient = tyrBase ? createApiClient(tyrBase) : null;
  const dispatcherBase = resolveTyrServiceBase(config, 'tyr.dispatcher');
  const dispatcherClient = dispatcherBase ? createApiClient(dispatcherBase) : null;
  const tyrSessionsBase = resolveTyrServiceBase(config, 'tyr.sessions');
  const tyrSessionsClient = tyrSessionsBase ? createApiClient(tyrSessionsBase) : null;
  const dispatchBase = resolveTyrServiceBase(config, 'tyr.dispatch');
  const dispatchClient = dispatchBase ? createApiClient(dispatchBase) : null;
  const tyrSettingsBase = resolveTyrServiceBase(config, 'tyr.settings');
  const tyrSettingsClient = tyrSettingsBase ? createApiClient(tyrSettingsBase) : null;
  const trackerBase = resolveCanonicalServiceBase(config, 'tracker');
  const trackerClient = trackerBase ? createApiClient(trackerBase) : null;
  const auditBase = resolveCanonicalServiceBase(config, 'audit');
  const auditClient = auditBase ? createApiClient(auditBase) : null;
  const workflowBase = resolveTyrServiceBase(config, 'tyr.workflows');
  const workflowClient = workflowBase ? createApiClient(workflowBase) : null;
  const tyrService = tyrClient ? buildTyrHttpAdapter(tyrClient) : createMockTyrService();
  const dispatcherService = dispatcherClient
    ? buildDispatcherHttpAdapter(dispatcherClient)
    : createMockDispatcherService();
  const tyrSessionService = tyrSessionsClient
    ? buildTyrSessionHttpAdapter(tyrSessionsClient)
    : createMockTyrSessionService();
  const trackerService = trackerClient
    ? buildTrackerHttpAdapter(trackerClient)
    : createMockTrackerService();
  const workflowService = workflowClient
    ? buildWorkflowHttpAdapter(workflowClient)
    : createMockWorkflowService();
  const dispatchBus = dispatchClient
    ? buildDispatchBusHttpAdapter(dispatchClient)
    : createMockDispatchBus();
  const tyrSettingsService = tyrSettingsClient
    ? buildTyrSettingsHttpAdapter(tyrSettingsClient)
    : createMockTyrSettingsService();
  const tyrAuditLogService = auditClient
    ? buildTyrAuditLogHttpAdapter(auditClient)
    : createMockAuditLogService();

  return {
    tyr: tyrService,
    'tyr.dispatcher': dispatcherService,
    'tyr.sessions': tyrSessionService,
    'tyr.tracker': trackerService,
    'tyr.workflows': workflowService,
    'tyr.dispatch': dispatchBus,
    'tyr.settings': tyrSettingsService,
    'tyr.audit': tyrAuditLogService,
    'ravn.personas': ravnPersonas,
    'ravn.ravens': ravnRavens,
    'ravn.sessions': ravnSessions,
    'ravn.triggers': ravnTriggers,
    'ravn.budget': ravnBudget,
    mimir,
    volundr,
    'niuu.repos': repoCatalogService,
    ptyStream,
    metricsStream,
    features: featureCatalogService,
    identity: identityService,
    filesystem,
    // NIU-678 pages (ClustersPage, TemplatesPage, HistoryPage)
    'volundr.clusters': clusterAdapter,
    'volundr.templates': templateStore,
    'volundr.sessions': sessionStore,
    // VolundrPage overview hooks (useVolundrClusters, useSessionStore)
    clusterAdapter,
    sessionStore,
    'observatory.registry': observatoryRegistry,
    'observatory.topology': observatoryTopology,
    'observatory.events': observatoryEvents,
  };
}
