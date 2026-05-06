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
import { SagaDetailPage } from './SagaDetailPage';
import { createMockTyrService } from '../adapters/mock';
import type { Saga, Phase } from '../domain/saga';

const STORY_SAGA_ID = '00000000-0000-0000-0000-000000000001';

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
    path: '/tyr/sagas/$sagaId',
    component: () => <>{children}</>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([pageRoute]),
    history: createMemoryHistory({
      initialEntries: [`/tyr/sagas/${STORY_SAGA_ID}`],
    }),
  });

  return (
    <QueryClientProvider client={client}>
      <ServicesProvider services={services}>
        <RouterProvider router={router} />
      </ServicesProvider>
    </QueryClientProvider>
  );
}

const meta: Meta<typeof SagaDetailPage> = {
  title: 'Plugins / Tyr / SagaDetailPage',
  component: SagaDetailPage,
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
type Story = StoryObj<typeof SagaDetailPage>;

/** Default — Auth Rewrite saga with 3 phases and 2 raids from seed data. */
export const Default: Story = {
  args: { sagaId: STORY_SAGA_ID },
};

/** Complete saga — all phases done, high confidence. */
export const CompleteSaga: Story = {
  args: { sagaId: '00000000-0000-0000-0000-000000000002' },
  decorators: [
    (Story) => (
      <StoryWrapper
        services={{
          tyr: {
            ...createMockTyrService(),
            getSaga: async (): Promise<Saga> => ({
              id: '00000000-0000-0000-0000-000000000002',
              trackerId: 'NIU-520',
              trackerType: 'linear',
              slug: 'plugin-ravn',
              name: 'Plugin Ravn Scaffold',
              repos: ['niuulabs/volundr'],
              featureBranch: 'feat/plugin-ravn',
              status: 'complete',
              confidence: 95,
              createdAt: '2026-01-05T08:00:00Z',
              phaseSummary: { total: 2, completed: 2 },
            }),
            getPhases: async (): Promise<Phase[]> => [
              {
                id: 'p1',
                sagaId: '00000000-0000-0000-0000-000000000002',
                trackerId: 'NIU-M1',
                number: 1,
                name: 'Phase 1: Scaffold',
                status: 'complete',
                confidence: 95,
                raids: [],
              },
              {
                id: 'p2',
                sagaId: '00000000-0000-0000-0000-000000000002',
                trackerId: 'NIU-M2',
                number: 2,
                name: 'Phase 2: Integration',
                status: 'complete',
                confidence: 95,
                raids: [],
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

/** Empty phases — no phases have been created yet. */
export const NoPhases: Story = {
  args: { sagaId: STORY_SAGA_ID },
  decorators: [
    (Story) => (
      <StoryWrapper
        services={{
          tyr: {
            ...createMockTyrService(),
            getPhases: async (): Promise<Phase[]> => [],
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
  args: { sagaId: STORY_SAGA_ID },
  decorators: [
    (Story) => (
      <StoryWrapper
        services={{
          tyr: {
            getSaga: () => new Promise(() => undefined),
            getPhases: () => new Promise(() => undefined),
          },
        }}
      >
        <Story />
      </StoryWrapper>
    ),
  ],
};

/** Not found — saga ID doesn't match any known saga. */
export const NotFound: Story = {
  args: { sagaId: 'nonexistent-saga' },
  decorators: [
    (Story) => (
      <StoryWrapper
        services={{
          tyr: {
            getSaga: async (): Promise<Saga | null> => null,
            getPhases: async (): Promise<Phase[]> => [],
          },
        }}
      >
        <Story />
      </StoryWrapper>
    ),
  ],
};
