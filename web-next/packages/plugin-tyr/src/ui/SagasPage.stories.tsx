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
import { SagasPage } from './SagasPage';
import { createMockTyrService } from '../adapters/mock';
import type { Saga } from '../domain/saga';

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
    path: '/tyr/sagas',
    component: () => <>{children}</>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([pageRoute]),
    history: createMemoryHistory({ initialEntries: ['/tyr/sagas'] }),
  });

  return (
    <QueryClientProvider client={client}>
      <ServicesProvider services={services}>
        <RouterProvider router={router} />
      </ServicesProvider>
    </QueryClientProvider>
  );
}

const meta: Meta<typeof SagasPage> = {
  title: 'Plugins / Tyr / SagasPage',
  component: SagasPage,
  parameters: { layout: 'fullscreen' },
  decorators: [
    (Story) => (
      <StoryWrapper services={{ tyr: createMockTyrService() }}>
        <Story />
      </StoryWrapper>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof SagasPage>;

/** Default with seed data: 1 active, 1 complete, 1 failed saga. */
export const Default: Story = {};

/** Only active sagas visible. */
export const OnlyActive: Story = {
  decorators: [
    (Story) => (
      <StoryWrapper
        services={{
          tyr: {
            ...createMockTyrService(),
            getSagas: async (): Promise<Saga[]> => [
              {
                id: '1',
                trackerId: 'NIU-001',
                trackerType: 'linear',
                slug: 'active-a',
                name: 'Active Saga A',
                repos: ['niuulabs/volundr'],
                featureBranch: 'feat/active-a',
                status: 'active',
                confidence: 78,
                createdAt: '2026-01-01T00:00:00Z',
                phaseSummary: { total: 3, completed: 1 },
              },
              {
                id: '2',
                trackerId: 'NIU-002',
                trackerType: 'linear',
                slug: 'active-b',
                name: 'Active Saga B',
                repos: ['niuulabs/volundr'],
                featureBranch: 'feat/active-b',
                status: 'active',
                confidence: 55,
                createdAt: '2026-01-05T00:00:00Z',
                phaseSummary: { total: 2, completed: 0 },
              },
            ],
          },
        }}
      >
        <Story />
      </StoryWrapper>
    ),
  ],
};

/** Empty state — no sagas at all. */
export const Empty: Story = {
  decorators: [
    (Story) => (
      <StoryWrapper
        services={{
          tyr: {
            getSagas: async (): Promise<Saga[]> => [],
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
          tyr: { getSagas: () => new Promise(() => undefined) },
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
          tyr: {
            getSagas: async () => {
              throw new Error('Tyr service unavailable');
            },
          },
        }}
      >
        <Story />
      </StoryWrapper>
    ),
  ],
};
