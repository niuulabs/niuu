import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { PersonaRole } from '@niuulabs/domain';
import type { DreamCycle } from '../domain/lint';
import type { RavnBinding, RavnState } from '../domain/ravn-binding';

interface WardenFeatures {
  wakefulnessEnabled: boolean;
  dreamCycleEnabled: boolean;
  threadQueueEnabled: boolean;
  threadEnricherEnabled: boolean;
  recapEnabled: boolean;
  sourceTriggerEnabled: boolean;
  stalenessTriggerEnabled: boolean;
}

interface WardenSchedules {
  dreamCycleCronExpression: string;
  dreamCyclePollIntervalSeconds: number;
  sourceTriggerPollIntervalSeconds: number;
  stalenessTriggerScheduleHours: number;
}

interface WardenConsole {
  enabled: boolean;
  host: string;
  port: number;
  publicHost?: string;
  authMode: 'noop' | 'token';
}

export interface WardenLogEntry {
  id: string;
  source: string;
  lineNumber: number;
  raw: string;
  timestamp?: string;
  level?: string;
  logger?: string;
  message: string;
}

export interface RavnWardenSummary {
  id: string;
  name: string;
  persona: string;
  profile: string;
  model: string;
  deployment: string;
  deploymentKwargs?: Record<string, unknown>;
  mountNames: string[];
  writeMount: string;
  readMountNames: string[];
  writeMountNames: string[];
  categoryScope: string[];
  features: WardenFeatures;
  schedules: WardenSchedules;
  console: WardenConsole;
  autostart: boolean;
  createdAt: string;
  createdBy: string;
  runtime?: {
    state?: RavnState;
    pagesTouched?: number;
    lastStartedAt?: string;
    lastDream?: DreamCycle | null;
  };
  supervisor?: {
    installed: boolean;
    serviceLabel?: string;
    serviceFile?: string;
    configFile?: string;
    startCommand?: string;
    stdoutLog?: string;
    stderrLog?: string;
    lastInstallAt?: string;
    observation?: {
      status: 'running' | 'idle' | 'missing' | 'degraded' | 'unknown';
      detail?: string;
      source?: string;
      checkedAt?: string;
      fields?: Array<{ label: string; value: string }>;
    };
  };
  operator?: {
    rune?: string;
    bio?: string;
    expertise?: string[];
    tools?: string[];
    role?: PersonaRole;
  };
}

export interface IRavnWardenService {
  listWardens(): Promise<RavnWardenSummary[]>;
  getWarden(id: string): Promise<RavnWardenSummary>;
  createWarden(req: {
    name: string;
    persona?: string;
    profile?: string;
    deployment?: string;
    deploymentKwargs?: Record<string, unknown>;
    model?: string;
    mountNames?: string[];
    writeMount?: string;
    readMountNames?: string[];
    writeMountNames?: string[];
    categoryScope?: string[];
    schedules?: Partial<WardenSchedules>;
    console?: Partial<WardenConsole>;
    autostart?: boolean;
    createdBy?: string;
  }): Promise<RavnWardenSummary>;
  subscribeWarden(id: string, listener: (warden: RavnWardenSummary) => void): () => void;
  observeWarden(id: string): Promise<RavnWardenSummary>;
  installWarden(id: string): Promise<RavnWardenSummary>;
  startWarden(id: string): Promise<RavnWardenSummary>;
  stopWarden(id: string): Promise<RavnWardenSummary>;
  uninstallWarden(id: string): Promise<RavnWardenSummary>;
  getWardenLogs(
    id: string,
    options?: { stream?: 'stdout' | 'stderr'; limit?: number },
  ): Promise<WardenLogEntry[]>;
  getWardenActivity(id: string, limit?: number): Promise<WardenLogEntry[]>;
}

const WARDENS_QUERY_KEY = ['ravn', 'wardens'] as const;

function wardenDetailKey(id: string) {
  return ['ravn', 'wardens', id, 'observe'] as const;
}

function upsertWarden(
  current: RavnWardenSummary[] | undefined,
  next: RavnWardenSummary,
): RavnWardenSummary[] {
  const existing = current ?? [];
  const updated = existing.some((warden) => warden.id === next.id)
    ? existing.map((warden) => (warden.id === next.id ? next : warden))
    : [...existing, next];
  return updated.sort(
    (left, right) => left.name.localeCompare(right.name) || left.id.localeCompare(right.id),
  );
}

function syncWardenCaches(queryClient: QueryClient, next: RavnWardenSummary): void {
  queryClient.setQueryData<RavnWardenSummary[]>(WARDENS_QUERY_KEY, (current) =>
    upsertWarden(current, next),
  );
  queryClient.setQueryData<RavnWardenSummary>(wardenDetailKey(next.id), next);
}

const PERSONA_ROLE_BY_NAME: Record<string, PersonaRole> = {
  'mimir-warden': 'knowledge',
  'research-and-distill': 'index',
  'mimir-curator': 'knowledge',
  'draft-a-note': 'write',
  'produce-recap': 'report',
  reporter: 'report',
  reviewer: 'review',
  'review-arbiter': 'review',
  verifier: 'verify',
  'qa-agent': 'verify',
  'planning-agent': 'plan',
  coordinator: 'coord',
  investigator: 'investigate',
  'health-auditor': 'observe',
};

function inferRole(persona: string): PersonaRole {
  const direct = PERSONA_ROLE_BY_NAME[persona];
  if (direct) return direct;

  const label = persona.toLowerCase();
  if (label.includes('index') || label.includes('curat') || label.includes('distill'))
    return 'index';
  if (label.includes('draft') || label.includes('write')) return 'write';
  if (label.includes('report') || label.includes('recap')) return 'report';
  if (label.includes('verify') || label.includes('qa') || label.includes('validat'))
    return 'verify';
  if (label.includes('plan')) return 'plan';
  if (label.includes('coord')) return 'coord';
  if (label.includes('investig') || label.includes('research')) return 'investigate';
  if (label.includes('observe') || label.includes('audit') || label.includes('health'))
    return 'observe';
  if (label.includes('review')) return 'review';
  return 'autonomy';
}

function inferState(warden: RavnWardenSummary): RavnState {
  if (warden.runtime?.state) return warden.runtime.state;
  if (!warden.deployment) return 'offline';
  return warden.autostart ? 'active' : 'idle';
}

function inferBio(warden: RavnWardenSummary): string {
  if (warden.operator?.bio) return warden.operator.bio;
  if (warden.profile) return `${warden.name} runs the ${warden.profile} profile.`;

  const mounts = warden.mountNames.length > 0 ? warden.mountNames.join(', ') : 'attached mounts';
  return `${warden.name} runs the ${warden.persona} persona across ${mounts}.`;
}

function inferExpertise(warden: RavnWardenSummary): string[] {
  if (warden.operator?.expertise?.length) return warden.operator.expertise;
  if (warden.categoryScope.length > 0) return warden.categoryScope;
  return [];
}

function inferTools(warden: RavnWardenSummary): string[] {
  if (warden.operator?.tools?.length) return warden.operator.tools;

  const tools = ['ravn', 'mimir'];
  if (warden.features.sourceTriggerEnabled) tools.push('web');
  if (warden.features.dreamCycleEnabled) tools.push('dream');
  return tools;
}

export function toRavnBinding(warden: RavnWardenSummary): RavnBinding {
  return {
    ravnId: warden.id,
    ravnRune: warden.operator?.rune ?? 'ᚱ',
    role: warden.operator?.role ?? inferRole(warden.persona),
    state: inferState(warden),
    mountNames: warden.mountNames,
    writeMount: warden.writeMount,
    lastDream: warden.runtime?.lastDream ?? null,
    bio: inferBio(warden),
    pagesTouched: warden.runtime?.pagesTouched ?? 0,
    expertise: inferExpertise(warden),
    tools: inferTools(warden),
  };
}

export function toRavnWardenSummary(binding: RavnBinding): RavnWardenSummary {
  const installed = binding.state !== 'offline';
  const configFile = `/Users/jozefvaneenbergen/.ravn/wardens/${binding.ravnId}/config.yaml`;
  return {
    id: binding.ravnId,
    name: binding.ravnId,
    persona: binding.role,
    profile: '',
    deployment: 'launchd',
    deploymentKwargs: {},
    model: 'claude-sonnet-4-6',
    mountNames: binding.mountNames,
    writeMount: binding.writeMount,
    readMountNames: binding.mountNames,
    writeMountNames: binding.writeMount ? [binding.writeMount] : [],
    categoryScope: binding.expertise,
    features: {
      wakefulnessEnabled: true,
      dreamCycleEnabled: true,
      threadQueueEnabled: true,
      threadEnricherEnabled: true,
      recapEnabled: true,
      sourceTriggerEnabled: true,
      stalenessTriggerEnabled: true,
    },
    schedules: {
      dreamCycleCronExpression: '0 3 * * *',
      dreamCyclePollIntervalSeconds: 60,
      sourceTriggerPollIntervalSeconds: 60,
      stalenessTriggerScheduleHours: 6,
    },
    console: {
      enabled: true,
      host: '0.0.0.0',
      port: 8400,
      authMode: 'noop',
    },
    autostart: binding.state === 'active',
    createdAt: new Date(0).toISOString(),
    createdBy: 'mimir-mock',
    runtime: {
      state: binding.state,
      pagesTouched: binding.pagesTouched,
      lastDream: binding.lastDream,
    },
    supervisor: {
      installed,
      serviceLabel: installed ? `dev.niuu.ravn.warden.${binding.ravnId}` : '',
      serviceFile: installed
        ? `/Users/jozefvaneenbergen/.ravn/wardens/${binding.ravnId}/warden.plist`
        : '',
      configFile: installed ? configFile : '',
      stdoutLog: installed
        ? `/Users/jozefvaneenbergen/.ravn/wardens/${binding.ravnId}/warden.log`
        : '',
      stderrLog: installed
        ? `/Users/jozefvaneenbergen/.ravn/wardens/${binding.ravnId}/warden.error.log`
        : '',
      startCommand: installed ? `ravn daemon --config ${configFile}` : '',
    },
    operator: {
      rune: binding.ravnRune,
      bio: binding.bio,
      expertise: binding.expertise,
      tools: binding.tools,
      role: binding.role,
    },
  };
}

export function useWardenDirectory() {
  const service = useService<IRavnWardenService>('ravn.wardens');
  return useQuery({
    queryKey: WARDENS_QUERY_KEY,
    queryFn: () => service.listWardens(),
  });
}

export function useObservedWarden(id: string | null) {
  const service = useService<IRavnWardenService>('ravn.wardens');
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!id) return;
    return service.subscribeWarden(id, (warden) => {
      syncWardenCaches(queryClient, warden);
    });
  }, [id, queryClient, service]);
  return useQuery({
    queryKey: id ? wardenDetailKey(id) : ['ravn', 'wardens', 'none', 'observe'],
    queryFn: () => service.observeWarden(id!),
    enabled: Boolean(id),
    retry: false,
  });
}

export function useRavns() {
  const query = useWardenDirectory();
  return {
    ...query,
    data: query.data?.map(toRavnBinding),
  };
}

export function useCreateWarden() {
  const service = useService<IRavnWardenService>('ravn.wardens');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (req: Parameters<IRavnWardenService['createWarden']>[0]) =>
      service.createWarden(req),
    onSuccess: (warden) => {
      syncWardenCaches(queryClient, warden);
    },
  });
}

export function useInstallWarden() {
  const service = useService<IRavnWardenService>('ravn.wardens');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => service.installWarden(id),
    onSuccess: (warden) => {
      syncWardenCaches(queryClient, warden);
    },
  });
}

export function useStartWarden() {
  const service = useService<IRavnWardenService>('ravn.wardens');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => service.startWarden(id),
    onSuccess: (warden) => {
      syncWardenCaches(queryClient, warden);
    },
  });
}

export function useStopWarden() {
  const service = useService<IRavnWardenService>('ravn.wardens');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => service.stopWarden(id),
    onSuccess: (warden) => {
      syncWardenCaches(queryClient, warden);
    },
  });
}

export function useUninstallWarden() {
  const service = useService<IRavnWardenService>('ravn.wardens');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => service.uninstallWarden(id),
    onSuccess: (warden) => {
      syncWardenCaches(queryClient, warden);
    },
  });
}

export function useWardenLogs(
  id: string | null,
  options: { stream?: 'stdout' | 'stderr'; limit?: number },
) {
  const service = useService<IRavnWardenService>('ravn.wardens');
  return useQuery({
    queryKey: ['ravn', 'wardens', id, 'logs', options.stream ?? 'stdout', options.limit ?? 200],
    queryFn: () => service.getWardenLogs(id!, options),
    enabled: Boolean(id),
    retry: false,
  });
}

export function useWardenActivity(id: string | null, limit = 120) {
  const service = useService<IRavnWardenService>('ravn.wardens');
  return useQuery({
    queryKey: ['ravn', 'wardens', id, 'activity', limit],
    queryFn: () => service.getWardenActivity(id!, limit),
    enabled: Boolean(id),
    retry: false,
  });
}
