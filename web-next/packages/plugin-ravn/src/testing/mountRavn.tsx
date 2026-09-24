/**
 * Mount the Ravn workbench and persona library under a memory router with
 * mocked service ports. Test-only.
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
import { RavnWorkbench } from '../ui/workbench/RavnWorkbench';
import { PersonaLibrary } from '../ui/library/PersonaLibrary';
import type { Ravn } from '../domain/ravn';
import type { Session } from '../domain/session';
import { makePersonaDetail, makePersonaSummary, makeProfile, makeRavn } from './fixtures';

export interface RavnServiceOptions {
  ravens?: Ravn[];
  sessions?: Session[];
  residentSessions?: Session[];
}

export function ravnServices(options: RavnServiceOptions = {}) {
  const ravens = options.ravens ?? [makeRavn()];
  return {
    'ravn.ravens': {
      listRavens: vi.fn().mockResolvedValue(ravens),
      getRaven: vi.fn(),
    },
    'ravn.residents': {
      listProfiles: vi.fn().mockResolvedValue([makeProfile()]),
      deploy: vi.fn().mockResolvedValue(makeRavn({ id: '99999999-9999-4999-8999-999999999999' })),
      applyLifecycle: vi.fn().mockImplementation(async (ravn: Ravn) => ravn),
      delete: vi.fn().mockResolvedValue(undefined),
      getLogs: vi.fn().mockResolvedValue({
        entries: [
          {
            timestampMs: Date.parse('2026-09-24T09:41:02Z'),
            level: 'INFO',
            source: 'ravn',
            target: 'ravn.agent',
            message: 'turn started',
            fields: { session: 'a1' },
          },
          {
            timestampMs: Date.parse('2026-09-24T09:41:06Z'),
            level: 'ERROR',
            source: 'ravn',
            target: 'ravn.tools',
            message: 'tool failed',
            fields: {},
          },
        ],
        bufferTotal: 2,
      }),
      listSessions: vi.fn().mockResolvedValue(options.residentSessions ?? []),
      createSession: vi.fn(),
      deleteSession: vi.fn().mockResolvedValue(undefined),
    },
    'ravn.sessions': {
      listSessions: vi.fn().mockResolvedValue(options.sessions ?? []),
      getSession: vi.fn(),
      getMessages: vi.fn().mockResolvedValue([]),
    },
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
    'ravn.budget': {
      getBudget: vi.fn().mockResolvedValue({ spentUsd: 1, capUsd: 4, warnAt: 0.8 }),
      getFleetBudget: vi.fn(),
    },
  };
}

export function mountRavn(path: string, services: Record<string, unknown>) {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const workbench = createRoute({
    getParentRoute: () => rootRoute,
    path: '/ravn',
    component: RavnWorkbench,
  });
  const personas = createRoute({
    getParentRoute: () => rootRoute,
    path: '/ravn/personas',
    component: PersonaLibrary,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([workbench, personas]),
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
