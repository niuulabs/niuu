/**
 * Mount the persona library under a memory router with mocked service
 * ports. Test-only.
 */
import { vi } from 'vitest';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { PersonaLibrary } from '../ui/library/PersonaLibrary';
import { makePersonaDetail, makePersonaSummary } from './fixtures';

export function ravnServices() {
  return {
    'ravn.personas': {
      listPersonas: vi.fn().mockResolvedValue([
        makePersonaSummary(),
        makePersonaSummary({
          name: 'research-framer',
          summary: 'Research framer',
          role: 'build',
        }),
        makePersonaSummary({
          name: 'research-analyst',
          summary: 'Research analyst',
          role: 'build',
        }),
      ]),
      getPersona: vi.fn().mockImplementation(async (name: string) => makePersonaDetail({ name })),
      getPersonaYaml: vi.fn().mockResolvedValue('name: reviewer\n'),
      createPersona: vi.fn(),
      updatePersona: vi.fn(),
      deletePersona: vi.fn().mockResolvedValue(undefined),
      forkPersona: vi.fn(),
    },
    'ravn.triggers': {
      listTriggers: vi.fn().mockResolvedValue([]),
      createTrigger: vi.fn(),
      deleteTrigger: vi.fn(),
    },
    bifrost: {
      listModels: vi.fn().mockResolvedValue([]),
    },
  };
}

export function mountRavn(path: string, services: Record<string, unknown>) {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const personas = createRoute({
    getParentRoute: () => rootRoute,
    path: '/ravn/personas',
    component: PersonaLibrary,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([personas]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={services}>
        <RouterProvider router={router} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return router;
}
