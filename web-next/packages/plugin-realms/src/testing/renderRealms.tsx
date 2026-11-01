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
import {
  PluginCtxProvider,
  ServicesProvider,
  createMockIdentityService,
  type PluginDescriptor,
} from '@niuulabs/plugin-sdk';
import { ShellContext, type ShellContextValue } from '@niuulabs/shell';
import { createMockBudgetStream, createMockSessionStream } from '@niuulabs/plugin-ravn';
import { createMockWorkflowService } from '@niuulabs/plugin-ting';
import {
  createMockOdinReviewService,
  createMockValkyrieService,
  createSeedRealms,
} from '@niuulabs/plugin-valkyrie';
import { useState } from 'react';
import { createMockSessionStore } from '@niuulabs/plugin-volundr';
import { homePlugin, realmsPlugin } from '../index';
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
    sessionStore: createMockSessionStore(),
    identity: createMockIdentityService(),
    ...overrides,
  };
}

/** The shell context the home page reads the enabled plugin list from. */
const SHELL_PLUGINS: PluginDescriptor[] = [homePlugin, realmsPlugin];

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
  // Stand-ins for the pages other plugins own, so the links on these pages resolve.
  const extra = [
    '/mimir',
    '/ravn/ravens',
    '/valkyrie/inbox',
    '/volundr/forge',
    '/volundr/sessions/new',
    '/volundr/sessions/$sessionId',
    '/ting/workflows',
  ].map((routePath) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path: routePath,
      component: () => <div data-testid={`route-${routePath.replace(/[^a-z]+/gi, '-')}`} />,
    }),
  );
  const routeTree = rootRoute.addChildren([
    ...realmsPlugin.routes!(rootRoute),
    ...homePlugin.routes!(rootRoute),
    ...extra,
  ]);
  const history = createMemoryHistory({ initialEntries: [path] });
  const router = createRouter({ routeTree, history });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const all = defaultServices(log, services);
  const shell: ShellContextValue = {
    enabled: SHELL_PLUGINS,
    brand: null,
    version: '0.0.0',
    ctx: { tweaks: {}, setTweak: () => {} },
  };
  const utils = render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={all}>
        <ShellContext.Provider value={shell}>
          <CtxProvider>
            <RouterProvider router={router} />
          </CtxProvider>
        </ShellContext.Provider>
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { ...utils, router, history, log, services: all };
}
