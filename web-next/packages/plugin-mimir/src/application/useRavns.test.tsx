import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PluginCtxProvider, ServicesProvider, type PluginCtx } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import {
  toRavnBinding,
  toRavnWardenSummary,
  useObservedWarden,
  type IRavnWardenService,
  type RavnWardenSummary,
} from './useRavns';

function renderWithWardens<T>(callback: () => T, service: IRavnWardenService) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const pluginCtx: PluginCtx = { tweaks: {}, setTweak: () => {} };
  return renderHook(callback, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>
        <PluginCtxProvider value={pluginCtx}>
          <ServicesProvider services={{ 'ravn.wardens': service }}>{children}</ServicesProvider>
        </PluginCtxProvider>
      </QueryClientProvider>
    ),
  });
}

function buildWarden(overrides: Partial<RavnWardenSummary> = {}): RavnWardenSummary {
  return {
    id: 'warden-test',
    name: 'Warden Test',
    persona: 'research-and-distill',
    profile: '',
    deployment: 'launchd',
    deploymentKwargs: {},
    mountNames: ['local'],
    writeMount: 'local',
    categoryScope: [],
    features: {
      wakefulnessEnabled: true,
      dreamCycleEnabled: true,
      threadQueueEnabled: true,
      threadEnricherEnabled: true,
      recapEnabled: true,
      sourceTriggerEnabled: true,
      stalenessTriggerEnabled: true,
    },
    autostart: true,
    createdAt: '2026-05-11T00:00:00Z',
    createdBy: 'test',
    runtime: {
      state: 'active',
      pagesTouched: 4,
      lastDream: null,
    },
    supervisor: {
      installed: true,
      serviceLabel: 'dev.niuu.ravn.warden.warden-test',
      serviceFile: '/tmp/warden-test.plist',
      configFile: '/tmp/warden-test.yaml',
      startCommand: 'ravn daemon --config /tmp/warden-test.yaml',
    },
    ...overrides,
  };
}

describe('useRavns', () => {
  it('infers fallback binding fields from persona, mounts, and features', () => {
    const binding = toRavnBinding(
      buildWarden({
        persona: 'research-investigator',
        deployment: '',
        autostart: false,
        profile: '',
        mountNames: ['scratch', 'permanent'],
        categoryScope: [],
        runtime: undefined,
        supervisor: undefined,
      }),
    );

    expect(binding).toMatchObject({
      ravnId: 'warden-test',
      ravnRune: 'ᚱ',
      role: 'investigate',
      state: 'offline',
      mountNames: ['scratch', 'permanent'],
      writeMount: 'local',
      bio: 'Warden Test runs the research-investigator persona across scratch, permanent.',
      expertise: [],
      tools: ['ravn', 'mimir', 'web', 'dream'],
    });
  });

  it('preserves explicit operator metadata and summary-derived state', () => {
    const binding = toRavnBinding(
      buildWarden({
        runtime: { state: 'idle', pagesTouched: 9, lastDream: null },
        operator: {
          rune: 'ᚦ',
          bio: 'Understands long-lived research memory.',
          expertise: ['research', 'memory'],
          tools: ['mimir', 'web'],
          role: 'index',
        },
      }),
    );

    expect(binding).toMatchObject({
      ravnRune: 'ᚦ',
      role: 'index',
      state: 'idle',
      bio: 'Understands long-lived research memory.',
      pagesTouched: 9,
      expertise: ['research', 'memory'],
      tools: ['mimir', 'web'],
    });
  });

  it('infers review and fallback autonomy roles from persona labels', () => {
    const reviewBinding = toRavnBinding(
      buildWarden({
        persona: 'council-review-chair',
        operator: undefined,
      }),
    );
    const autonomyBinding = toRavnBinding(
      buildWarden({
        persona: 'lorekeeper',
        operator: undefined,
      }),
    );

    expect(reviewBinding.role).toBe('review');
    expect(autonomyBinding.role).toBe('autonomy');
  });

  it('maps bindings back to installed and offline warden summaries', () => {
    expect(
      toRavnWardenSummary({
        ravnId: 'active-warden',
        ravnRune: 'ᚹ',
        role: 'report',
        state: 'active',
        mountNames: ['local'],
        writeMount: 'local',
        lastDream: null,
        bio: 'Summarizes overnight changes.',
        pagesTouched: 2,
        expertise: ['recap'],
        tools: ['mimir'],
      }),
    ).toMatchObject({
      deployment: 'launchd',
      autostart: true,
      supervisor: {
        installed: true,
        serviceLabel: 'dev.niuu.ravn.warden.active-warden',
      },
      operator: { role: 'report' },
    });

    expect(
      toRavnWardenSummary({
        ravnId: 'offline-warden',
        ravnRune: 'ᚱ',
        role: 'observe',
        state: 'offline',
        mountNames: [],
        writeMount: '',
        lastDream: null,
        bio: 'Offline test.',
        pagesTouched: 0,
        expertise: [],
        tools: [],
      }),
    ).toMatchObject({
      autostart: false,
      supervisor: {
        installed: false,
        serviceLabel: '',
      },
    });
  });

  it('disables observed queries when no warden id is selected', async () => {
    const service: IRavnWardenService = {
      listWardens: async () => [],
      getWarden: async () => buildWarden(),
      createWarden: async () => buildWarden(),
      subscribeWarden: () => () => {},
      observeWarden: vi.fn(async () => buildWarden()),
      installWarden: async () => buildWarden(),
      startWarden: async () => buildWarden(),
      stopWarden: async () => buildWarden(),
      uninstallWarden: async () => buildWarden(),
    };

    const { result } = renderWithWardens(() => useObservedWarden(null), service);

    await waitFor(() => expect(result.current.isSuccess).toBe(false));
    expect(service.observeWarden).not.toHaveBeenCalled();
  });

  it('hydrates observed wardens and syncs subscription updates into the query cache', async () => {
    let listener: ((warden: RavnWardenSummary) => void) | undefined;
    const service: IRavnWardenService = {
      listWardens: async () => [],
      getWarden: async () => buildWarden(),
      createWarden: async () => buildWarden(),
      subscribeWarden: (_id, next) => {
        listener = next;
        return () => {
          listener = undefined;
        };
      },
      observeWarden: vi.fn(async () => buildWarden()),
      installWarden: async () => buildWarden(),
      startWarden: async () => buildWarden(),
      stopWarden: async () => buildWarden(),
      uninstallWarden: async () => buildWarden(),
    };

    const { result } = renderWithWardens(() => useObservedWarden('warden-test'), service);

    await waitFor(() => expect(result.current.data?.runtime?.pagesTouched).toBe(4));
    act(() => {
      listener?.(
        buildWarden({
          runtime: { state: 'active', pagesTouched: 12, lastDream: null },
        }),
      );
    });

    await waitFor(() => expect(result.current.data?.runtime?.pagesTouched).toBe(12));
    expect(service.observeWarden).toHaveBeenCalledWith('warden-test');
  });
});
