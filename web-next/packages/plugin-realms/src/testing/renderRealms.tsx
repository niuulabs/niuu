import { render, type RenderResult } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  type AnyRouter,
  type RouterHistory,
} from '@tanstack/react-router';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { PluginCtxProvider, ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBudgetStream, createMockSessionStream } from '@niuulabs/plugin-ravn';
import { createMockWorkflowService } from '@niuulabs/plugin-ting';
import {
  createMockOdinReviewService,
  createMockValkyrieService,
  createSeedRealms,
} from '@niuulabs/plugin-valkyrie';
import { useState } from 'react';
import { realmsPlugin } from '../index';
import {
  createCallLog,
  fakeMimir,
  fakePersonas,
  fakeRealmService,
  fakeResidents,
  fakeTracker,
  fakeTriggers,
  fakeVolundr,
  type CallLog,
} from './fakes';

export function defaultServices(log: CallLog, overrides: Record<string, unknown> = {}) {
  const residents = fakeResidents(log);
  return {
    'valkyrie.realms': fakeRealmService(log, createSeedRealms()),
    valkyrie: createMockValkyrieService(),
    'valkyrie.reviews': createMockOdinReviewService(),
    'ravn.personas': fakePersonas(log),
    'ravn.triggers': fakeTriggers(log),
    'ravn.residents': residents,
    'ravn.ravens': residents,
    'ravn.sessions': createMockSessionStream(),
    'ravn.budget': createMockBudgetStream(),
    'ting.tracker': fakeTracker(log),
    'ting.workflows': createMockWorkflowService(),
    volundr: fakeVolundr(log),
    'niuu.repos': {
      getRepos: () => fakeVolundr(log).getRepos(),
      getBranches: async () => ['dev', 'main'],
    },
    mimir: fakeMimir(log),
    bifrost: createMockBifrostService(),
    ...overrides,
  };
}

function CtxProvider({ children }: { children: React.ReactNode }) {
  const [tweaks, setTweaks] = useState<Record<string, unknown>>({});
  return (
    <PluginCtxProvider
      value={{ tweaks, setTweak: (key, value) => setTweaks((t) => ({ ...t, [key]: value })) }}
    >
      {children}
    </PluginCtxProvider>
  );
}

export interface RenderRealmsResult extends RenderResult {
  router: AnyRouter;
  history: RouterHistory;
  log: CallLog;
  services: Record<string, unknown>;
}

/** Mounts the Realms plugin's routes in a memory router at `path` with fake services. */
export function renderRealms(
  path: string,
  services: Record<string, unknown> = {},
  log: CallLog = createCallLog(),
): RenderRealmsResult {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const extra = [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/mimir/pages',
      component: () => <div data-testid="mimir-pages" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ravn/ravens',
      component: () => <div data-testid="ravn-ravens" />,
    }),
  ];
  const routeTree = rootRoute.addChildren([...realmsPlugin.routes!(rootRoute), ...extra]);
  const history = createMemoryHistory({ initialEntries: [path] });
  const router = createRouter({ routeTree, history });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const all = defaultServices(log, services);
  const utils = render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={all}>
        <CtxProvider>
          <RouterProvider router={router} />
        </CtxProvider>
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { ...utils, router, history, log, services: all };
}
