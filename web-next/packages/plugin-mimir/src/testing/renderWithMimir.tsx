import { render, type RenderResult } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PluginCtxProvider, ServicesProvider, type PluginCtx } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { createMockVolundrService, type IVolundrService } from '@niuulabs/plugin-volundr';
import { createMockPersonaStore } from '../../../plugin-ravn/src/adapters/mock';
import { createMimirMockAdapter } from '../adapters/mock';
import {
  toRavnWardenSummary,
  type IRavnWardenService,
  type RavnWardenSummary,
  type WardenLogEntry,
} from '../application/useRavns';
import type { IMimirService } from '../ports';

function slugify(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function createStatefulWardenService(svc: IMimirService): IRavnWardenService {
  let pendingWardens: RavnWardenSummary[] | null = null;
  const listeners = new Map<string, Set<(warden: RavnWardenSummary) => void>>();

  async function ensureWardens() {
    if (pendingWardens) return pendingWardens;
    const bindings = await svc.mounts.listRavnBindings();
    pendingWardens = bindings.map(toRavnWardenSummary);
    return pendingWardens;
  }

  function emit(warden: RavnWardenSummary) {
    const subscribers = listeners.get(warden.id);
    if (!subscribers) return;
    for (const listener of subscribers) listener(warden);
  }

  function sampleLogEntries(
    warden: RavnWardenSummary,
    source: 'stdout' | 'stderr',
  ): WardenLogEntry[] {
    const now = new Date().toISOString();
    if (source === 'stderr') {
      return [
        {
          id: `${warden.id}-stderr-1`,
          source,
          lineNumber: 1,
          raw: '',
          timestamp: now,
          level: 'WARN',
          logger: 'ravn.daemon',
          message: 'No stderr events recorded in the test daemon.',
        },
      ];
    }
    return [
      {
        id: `${warden.id}-stdout-1`,
        source,
        lineNumber: 1,
        raw: '',
        timestamp: now,
        level: 'INFO',
        logger: 'ravn.daemon',
        message: 'ravn daemon started.',
      },
      {
        id: `${warden.id}-stdout-2`,
        source,
        lineNumber: 2,
        raw: '',
        timestamp: now,
        level: 'INFO',
        logger: 'ravn.daemon',
        message: 'Drive loop: running (max 3 concurrent tasks)',
      },
    ];
  }

  return {
    async listWardens() {
      return [...(await ensureWardens())];
    },
    async getWarden(id) {
      const wardens = await ensureWardens();
      const current = wardens.find((warden) => warden.id === id);
      if (!current) throw new Error(`Warden not found: ${id}`);
      return current;
    },
    async createWarden(req) {
      const wardens = await ensureWardens();
      const baseId = slugify(req.name) || 'warden';
      const taken = new Set(wardens.map((warden) => warden.id));
      let nextId = baseId;
      let suffix = 2;
      while (taken.has(nextId)) {
        nextId = `${baseId}-${suffix++}`;
      }
      const created: RavnWardenSummary = {
        id: nextId,
        name: req.name,
        persona: req.persona ?? 'mimir-warden',
        profile: req.profile ?? '',
        deployment: req.deployment ?? 'launchd',
        deploymentKwargs: req.deploymentKwargs ?? {},
        model: req.model ?? 'claude-sonnet-4-6',
        mountNames: req.mountNames ?? [],
        writeMount: req.writeMount ?? req.mountNames?.[0] ?? '',
        readMountNames: req.readMountNames ?? req.mountNames ?? [],
        writeMountNames: req.writeMountNames ?? (req.writeMount ? [req.writeMount] : []),
        categoryScope: req.categoryScope ?? [],
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
          dreamCycleCronExpression: req.schedules?.dreamCycleCronExpression ?? '0 3 * * *',
          dreamCyclePollIntervalSeconds: req.schedules?.dreamCyclePollIntervalSeconds ?? 60,
          sourceTriggerPollIntervalSeconds: req.schedules?.sourceTriggerPollIntervalSeconds ?? 60,
          stalenessTriggerScheduleHours: req.schedules?.stalenessTriggerScheduleHours ?? 6,
        },
        console: {
          enabled: req.console?.enabled ?? true,
          host: req.console?.host ?? '0.0.0.0',
          port: req.console?.port ?? 8400,
          publicHost: req.console?.publicHost ?? '',
          authMode: req.console?.authMode ?? 'noop',
        },
        autostart: req.autostart ?? false,
        createdAt: new Date().toISOString(),
        createdBy: req.createdBy ?? 'mimir-test',
        runtime: {
          state: 'offline' as const,
          pagesTouched: 0,
          lastDream: null,
        },
        supervisor: {
          installed: false,
        },
      };
      pendingWardens = [...wardens, created];
      emit(created);
      return created;
    },
    subscribeWarden(id, listener) {
      const subscribers = listeners.get(id) ?? new Set();
      subscribers.add(listener);
      listeners.set(id, subscribers);
      return () => {
        const current = listeners.get(id);
        if (!current) return;
        current.delete(listener);
        if (current.size === 0) listeners.delete(id);
      };
    },
    async observeWarden(id) {
      const wardens = await ensureWardens();
      const current = wardens.find((warden) => warden.id === id);
      if (!current) {
        throw new Error(`Warden not found: ${id}`);
      }
      const observed: RavnWardenSummary = {
        ...current,
        supervisor: {
          installed: Boolean(current.supervisor?.installed),
          serviceLabel: current.supervisor?.serviceLabel,
          serviceFile: current.supervisor?.serviceFile,
          configFile: current.supervisor?.configFile,
          startCommand: current.supervisor?.startCommand,
          lastInstallAt: current.supervisor?.lastInstallAt,
          ...current.supervisor,
          observation: {
            status:
              current.runtime?.state === 'active'
                ? 'running'
                : current.supervisor?.installed
                  ? 'idle'
                  : 'missing',
            detail:
              current.runtime?.state === 'active'
                ? 'Test backend reports the warden as running'
                : current.supervisor?.installed
                  ? 'Test backend reports the warden as installed but idle'
                  : 'Test backend reports no installed deployment',
            source: current.deployment.startsWith('k8s') ? 'test-kubernetes' : 'test-supervisor',
            checkedAt: new Date().toISOString(),
            fields: [],
          },
        },
      };
      pendingWardens = wardens.map((warden) => (warden.id === id ? observed : warden));
      emit(observed);
      return observed;
    },
    async installWarden(id) {
      const wardens = await ensureWardens();
      const nextStateFor = (warden: RavnWardenSummary): 'active' | 'idle' =>
        warden.runtime?.state === 'active' ? 'active' : 'idle';
      const updated = wardens.map((warden) =>
        warden.id === id
          ? {
              ...warden,
              runtime: {
                ...warden.runtime,
                state: nextStateFor(warden),
              },
              supervisor: {
                installed: true,
                serviceLabel: `dev.niuu.ravn.warden.${id}`,
                serviceFile: warden.deployment.startsWith('k8s')
                  ? `/tmp/${id}.yaml`
                  : `/tmp/${id}.${warden.deployment === 'systemd' ? 'service' : 'plist'}`,
                configFile: `/tmp/${id}.yaml`,
                stdoutLog: `/tmp/${id}.stdout.log`,
                stderrLog: `/tmp/${id}.stderr.log`,
                startCommand: warden.deployment.startsWith('k8s')
                  ? warden.deployment
                  : `ravn daemon --config /tmp/${id}.yaml --persona ${warden.persona}`,
                lastInstallAt: new Date().toISOString(),
              },
            }
          : warden,
      );
      pendingWardens = updated;
      const installed = updated.find((warden) => warden.id === id)!;
      emit(installed);
      return installed;
    },
    async startWarden(id) {
      const wardens = await ensureWardens();
      const current = wardens.find((warden) => warden.id === id);
      if (!current?.supervisor?.installed) {
        throw new Error('Warden must be installed before it can be started');
      }
      const updated = wardens.map((warden) =>
        warden.id === id
          ? {
              ...warden,
              runtime: {
                ...warden.runtime,
                state: 'active' as const,
                lastStartedAt: new Date().toISOString(),
              },
            }
          : warden,
      );
      pendingWardens = updated;
      const started = updated.find((warden) => warden.id === id)!;
      emit(started);
      return started;
    },
    async stopWarden(id) {
      const wardens = await ensureWardens();
      const current = wardens.find((warden) => warden.id === id);
      if (!current?.supervisor?.installed) {
        throw new Error('Warden must be installed before it can be stopped');
      }
      const updated = wardens.map((warden) =>
        warden.id === id
          ? {
              ...warden,
              runtime: {
                ...warden.runtime,
                state: 'idle' as const,
              },
            }
          : warden,
      );
      pendingWardens = updated;
      const stopped = updated.find((warden) => warden.id === id)!;
      emit(stopped);
      return stopped;
    },
    async uninstallWarden(id) {
      const wardens = await ensureWardens();
      const current = wardens.find((warden) => warden.id === id);
      if (!current) {
        throw new Error(`Warden not found: ${id}`);
      }
      const updated = wardens.map((warden) =>
        warden.id === id
          ? {
              ...warden,
              runtime: {
                ...warden.runtime,
                state: 'offline' as const,
              },
              supervisor: {
                installed: false,
              },
            }
          : warden,
      );
      pendingWardens = updated;
      const uninstalled = updated.find((warden) => warden.id === id)!;
      emit(uninstalled);
      return uninstalled;
    },
    async getWardenLogs(id, options) {
      const wardens = await ensureWardens();
      const current = wardens.find((warden) => warden.id === id);
      if (!current) throw new Error(`Warden not found: ${id}`);
      return sampleLogEntries(current, options?.stream ?? 'stdout').slice(0, options?.limit ?? 200);
    },
    async getWardenActivity(id, limit = 120) {
      const wardens = await ensureWardens();
      const current = wardens.find((warden) => warden.id === id);
      if (!current) throw new Error(`Warden not found: ${id}`);
      return sampleLogEntries(current, 'stdout')
        .concat([
          {
            id: `${current.id}-activity-3`,
            source: 'stderr',
            lineNumber: 3,
            raw: '',
            timestamp: new Date().toISOString(),
            level: current.runtime?.state === 'active' ? 'INFO' : 'WARN',
            logger: 'ravn.dream',
            message:
              current.runtime?.state === 'active'
                ? 'dream cycle scheduled and waiting for next cron window'
                : 'warden is installed but idle',
          },
        ])
        .slice(0, limit);
    },
  };
}

export function renderWithMimir(
  ui: React.ReactNode,
  service?: IMimirService,
  ctx: Partial<PluginCtx> = {},
  wardenService?: IRavnWardenService,
  volundrService: IVolundrService = createMockVolundrService(),
): RenderResult {
  const svc = service ?? createMimirMockAdapter();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const pluginCtx: PluginCtx = {
    tweaks: {},
    setTweak: () => {},
    ...ctx,
  };
  const ravnWardens = wardenService ?? createStatefulWardenService(svc);
  return render(
    <QueryClientProvider client={client}>
      <PluginCtxProvider value={pluginCtx}>
        <ServicesProvider
          services={{
            bifrost: createMockBifrostService(),
            mimir: svc,
            'ravn.wardens': ravnWardens,
            'ravn.personas': createMockPersonaStore(),
            volundr: volundrService,
          }}
        >
          {ui}
        </ServicesProvider>
      </PluginCtxProvider>
    </QueryClientProvider>,
  );
}
