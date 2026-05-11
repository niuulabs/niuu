import { render, type RenderResult } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PluginCtxProvider, ServicesProvider, type PluginCtx } from '@niuulabs/plugin-sdk';
import { createMimirMockAdapter } from '../adapters/mock';
import {
  toRavnWardenSummary,
  type IRavnWardenService,
  type RavnWardenSummary,
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
        persona: req.persona ?? 'research-and-distill',
        profile: req.profile ?? '',
        deployment: req.deployment ?? 'launchd',
        deploymentKwargs: req.deploymentKwargs ?? {},
        mountNames: req.mountNames ?? [],
        writeMount: req.writeMount ?? req.mountNames?.[0] ?? '',
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
  };
}

export function renderWithMimir(
  ui: React.ReactNode,
  service?: IMimirService,
  ctx: Partial<PluginCtx> = {},
  wardenService?: IRavnWardenService,
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
        <ServicesProvider services={{ mimir: svc, 'ravn.wardens': ravnWardens }}>
          {ui}
        </ServicesProvider>
      </PluginCtxProvider>
    </QueryClientProvider>,
  );
}
