import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import {
  createRouter,
  RouterProvider,
  createRootRoute,
  createRoute,
  createMemoryHistory,
  Outlet,
} from '@tanstack/react-router';
import { DashboardPage } from './DashboardPage';
import { createMockTingService, createMockDispatcherService } from '../adapters/mock';
import type { Saga } from '../domain/saga';
import type { DispatcherState } from '../domain/dispatcher';

// ---------------------------------------------------------------------------
// Story wrapper — provides QueryClient + Services + minimal TanStack Router
// ---------------------------------------------------------------------------

function StoryWrapper({
  children,
  services,
}: {
  children: React.ReactNode;
  services: Record<string, unknown>;
}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });

  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const pageRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/ting',
    component: () => <>{children}</>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([pageRoute]),
    history: createMemoryHistory({ initialEntries: ['/ting'] }),
  });

  return (
    <QueryClientProvider client={client}>
      <ServicesProvider services={services}>
        <RouterProvider router={router} />
      </ServicesProvider>
    </QueryClientProvider>
  );
}

const meta: Meta<typeof DashboardPage> = {
  title: 'Plugins / Ting / DashboardPage',
  component: DashboardPage,
  parameters: { layout: 'fullscreen' },
  decorators: [
    (Story) => (
      <StoryWrapper
        services={{
          ting: createMockTingService(),
          'ting.dispatcher': createMockDispatcherService(),
        }}
      >
        <Story />
      </StoryWrapper>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof DashboardPage>;

/** Default with seed data: 1 active saga, 1 complete, 1 failed. */
export const Default: Story = {};

/** All sagas are complete — active section shows empty state. */
export const AllComplete: Story = {
  decorators: [
    (Story) => (
      <StoryWrapper
        services={{
          ting: {
            ...createMockTingService(),
            getSagas: async (): Promise<Saga[]> => [
              {
                id: '1',
                trackerId: 'NIU-100',
                trackerType: 'linear',
                slug: 'done-a',
                name: 'Auth Rewrite',
                repos: [],
                featureBranch: 'feat/auth',
                status: 'complete',
                confidence: 92,
                createdAt: '2026-01-01T00:00:00Z',
                phaseSummary: { total: 3, completed: 3 },
              },
            ],
            getPhases: async () => [],
          },
          'ting.dispatcher': createMockDispatcherService(),
        }}
      >
        <Story />
      </StoryWrapper>
    ),
  ],
};

/** Dispatcher is stopped — shows "Stopped" status. */
export const DispatcherStopped: Story = {
  decorators: [
    (Story) => (
      <StoryWrapper
        services={{
          ting: createMockTingService(),
          'ting.dispatcher': {
            ...createMockDispatcherService(),
            getState: async (): Promise<DispatcherState> => ({
              id: '00000000-0000-0000-0000-000000000999',
              running: false,
              threshold: 70,
              maxConcurrentRuns: 3,
              autoContinue: false,
              updatedAt: '2026-01-01T00:00:00Z',
            }),
          },
        }}
      >
        <Story />
      </StoryWrapper>
    ),
  ],
};

/** Loading state — data hasn't resolved. */
export const Loading: Story = {
  decorators: [
    (Story) => (
      <StoryWrapper
        services={{
          ting: {
            getSagas: () => new Promise(() => undefined),
            getPhases: () => Promise.resolve([]),
          },
          'ting.dispatcher': { getState: () => new Promise(() => undefined) },
        }}
      >
        <Story />
      </StoryWrapper>
    ),
  ],
};

/** Error state — service throws. */
export const ServiceError: Story = {
  decorators: [
    (Story) => (
      <StoryWrapper
        services={{
          ting: {
            getSagas: async () => {
              throw new Error('Ting service unavailable');
            },
            getPhases: () => Promise.resolve([]),
          },
          'ting.dispatcher': createMockDispatcherService(),
        }}
      >
        <Story />
      </StoryWrapper>
    ),
  ],
};
