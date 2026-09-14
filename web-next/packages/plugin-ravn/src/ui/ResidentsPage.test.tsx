import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
import type { PersonaSummary } from '@niuulabs/domain';
import { ResidentsPage } from './ResidentsPage';
import type { Ravn } from '../domain/ravn';
import type { Session } from '../domain/session';

function makeRavn(overrides: Partial<Ravn> = {}): Ravn {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    personaName: 'realm-lexi-api',
    residentName: 'lexi-api',
    status: 'active',
    model: 'gpt-5.6',
    createdAt: '2026-07-13T10:00:00Z',
    updatedAt: '2026-09-14T07:00:00Z',
    kind: 'resident',
    managed: true,
    desiredState: 'running',
    observedState: 'active',
    capabilities: ['runtime.restart', 'runtime.suspend', 'session.list', 'session.create'],
    instanceId: 'target-a',
    ...overrides,
  } as Ravn;
}

function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    id: '22222222-2222-4222-8222-222222222222',
    ravnId: '11111111-1111-4111-8111-111111111111',
    personaName: 'realm-lexi-api',
    status: 'running',
    model: 'gpt-5.6',
    createdAt: '2026-09-14T06:00:00Z',
    instanceId: 'target-a',
    ...overrides,
  } as Session;
}

function makePersona(overrides: Partial<PersonaSummary> = {}): PersonaSummary {
  return {
    name: 'realm-lexi-api',
    role: 'build',
    letter: 'L',
    color: '#fff',
    summary: 'keeps the Lexi API healthy',
    permissionMode: 'ask',
    allowedTools: [],
    iterationBudget: 10,
    isBuiltin: false,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
    ...overrides,
  };
}

const VALKYRIES = [
  {
    id: 'v-1',
    name: 'muninn',
    environmentId: 'env-k8s-lexi-api',
    wakefulness: 'watching',
    lastActionAt: '2026-09-14T07:30:00Z',
  },
];

function makeServices(overrides: Record<string, unknown> = {}) {
  return {
    'ravn.ravens': { listRavens: vi.fn().mockResolvedValue([makeRavn()]), getRaven: vi.fn() },
    'ravn.personas': { listPersonas: vi.fn().mockResolvedValue([makePersona()]) },
    'ravn.sessions': {
      listSessions: vi.fn().mockResolvedValue([]),
      getSession: vi.fn(),
      getMessages: vi.fn(),
    },
    'ravn.residents': {
      listProfiles: vi.fn().mockResolvedValue([]),
      deploy: vi.fn(),
      applyLifecycle: vi.fn().mockResolvedValue(makeRavn()),
      delete: vi.fn(),
      getLogs: vi.fn(),
      listSessions: vi.fn().mockResolvedValue([]),
      createSession: vi.fn(),
      deleteSession: vi.fn(),
    },
    ...overrides,
  };
}

function valkyrieServices(overrides: Record<string, unknown> = {}) {
  return makeServices({
    valkyrie: { getDashboard: vi.fn().mockResolvedValue({ valkyries: VALKYRIES, actions: [] }) },
    'valkyrie.reviews': { listReviews: vi.fn().mockResolvedValue([]) },
    'valkyrie.realms': {
      listRealms: vi.fn().mockResolvedValue([{ slug: 'lexi-api', name: 'Lexi API' }]),
    },
    ...overrides,
  });
}

function mount(services: Record<string, unknown>) {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const residents = createRoute({
    getParentRoute: () => rootRoute,
    path: '/ravn/residents',
    component: ResidentsPage,
  });
  const personas = createRoute({
    getParentRoute: () => rootRoute,
    path: '/ravn/personas',
    component: () => <div data-testid="personas-route" />,
  });
  const realm = createRoute({
    getParentRoute: () => rootRoute,
    path: '/realms/$slug',
    component: () => <div data-testid="realm-route" />,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([residents, personas, realm]),
    history: createMemoryHistory({ initialEntries: ['/ravn/residents'] }),
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

beforeEach(() => {
  localStorage.clear();
  window.history.pushState(null, '', '/ravn/residents');
});

describe('ResidentsPage', () => {
  it('leads with what a resident is and offers a new one', async () => {
    mount(valkyrieServices());
    expect(await screen.findByRole('heading', { name: 'Residents' })).toBeInTheDocument();
    expect(screen.getByText(/A resident keeps one realm/)).toBeInTheDocument();
    expect(screen.getByText(/A persona is the character it runs with/)).toBeInTheDocument();
    expect(screen.getByTestId('resident-deploy-open')).toBeInTheDocument();
  });

  it('shows the realm it keeps, its wakefulness and that nothing is waiting', async () => {
    mount(valkyrieServices());
    const card = await screen.findByTestId(
      'resident-card-target-a:11111111-1111-4111-8111-111111111111',
    );
    expect(within(card).getByText('lexi-api')).toBeInTheDocument();
    expect(await within(card).findByText('keeps Lexi API')).toBeInTheDocument();
    expect(await within(card).findByText('watching')).toBeInTheDocument();
    expect(within(card).getByText('gpt-5.6')).toBeInTheDocument();
    expect(within(card).getByText('nothing waiting')).toBeInTheDocument();
    expect(within(card).getByTestId('resident-open-realm')).toHaveAttribute(
      'href',
      '/realms/lexi-api',
    );
  });

  it('counts the reviews waiting on the resident’s environment', async () => {
    mount(
      valkyrieServices({
        'valkyrie.reviews': {
          listReviews: vi.fn().mockResolvedValue([
            { itemId: 'r1', environmentId: 'env-k8s-lexi-api' },
            { itemId: 'r2', environmentId: 'env-k8s-lexi-api' },
            { itemId: 'r3', environmentId: 'env-k8s-other' },
          ]),
        },
      }),
    );
    expect(await screen.findByTestId('resident-needs-you')).toHaveTextContent('2 need you');
  });

  it('names the last thing the resident did', async () => {
    mount(
      valkyrieServices({
        valkyrie: {
          getDashboard: vi.fn().mockResolvedValue({
            valkyries: VALKYRIES,
            actions: [
              {
                id: 'a1',
                title: 'Rotated the staging certificate',
                ownerValkyrieId: 'v-1',
                environmentId: 'env-k8s-lexi-api',
                finishedAt: new Date().toISOString(),
              },
            ],
          }),
        },
      }),
    );
    expect(await screen.findByText(/Rotated the staging certificate/)).toBeInTheDocument();
  });

  it('orders the ones that need an answer first, then the awake ones, then by name', async () => {
    const waiting = makeRavn({
      id: '33333333-3333-4333-8333-333333333333',
      residentName: 'zeta',
      personaName: 'realm-other',
      status: 'idle',
      instanceId: undefined,
    });
    const sleeping = makeRavn({
      id: '44444444-4444-4444-8444-444444444444',
      residentName: 'alpha',
      personaName: 'realm-alpha',
      status: 'idle',
      instanceId: undefined,
    });
    mount(
      valkyrieServices({
        'ravn.ravens': {
          listRavens: vi.fn().mockResolvedValue([sleeping, makeRavn(), waiting]),
          getRaven: vi.fn(),
        },
        valkyrie: {
          getDashboard: vi.fn().mockResolvedValue({
            valkyries: [
              ...VALKYRIES,
              { id: 'v-2', name: 'zeta', environmentId: 'env-other', wakefulness: 'sleeping' },
            ],
            actions: [],
          }),
        },
        'valkyrie.reviews': {
          listReviews: vi.fn().mockResolvedValue([{ itemId: 'r1', environmentId: 'env-other' }]),
        },
      }),
    );
    await screen.findByTestId('resident-needs-you');
    const cards = screen.getAllByTestId(/^resident-card-/);
    expect(cards).toHaveLength(3);
    expect(within(cards[0]!).getByText('zeta')).toBeInTheDocument();
    expect(within(cards[1]!).getByText('lexi-api')).toBeInTheDocument();
    expect(within(cards[2]!).getByText('alpha')).toBeInTheDocument();
  });

  it('leaves persona ravens off the board', async () => {
    mount(
      valkyrieServices({
        'ravn.ravens': {
          listRavens: vi
            .fn()
            .mockResolvedValue([
              makeRavn({ kind: 'persona', managed: false, residentName: 'ghost' }),
            ]),
          getRaven: vi.fn(),
        },
      }),
    );
    expect(await screen.findByText('No residents yet')).toBeInTheDocument();
  });

  it('falls back to the ravn status and hides realm links without the Valkyrie services', async () => {
    mount(makeServices());
    const card = await screen.findByTestId(
      'resident-card-target-a:11111111-1111-4111-8111-111111111111',
    );
    expect(within(card).getByText('active')).toBeInTheDocument();
    expect(within(card).getByText('keeps no realm yet')).toBeInTheDocument();
    expect(within(card).queryByTestId('resident-open-realm')).not.toBeInTheDocument();
    expect(within(card).getByText('nothing waiting')).toBeInTheDocument();
  });

  it('pauses a resident and shows the command as taken before the backend answers', async () => {
    const applyLifecycle = vi.fn().mockReturnValue(new Promise(() => undefined));
    mount(
      valkyrieServices({
        'ravn.residents': { ...makeServices()['ravn.residents'], applyLifecycle },
      }),
    );

    fireEvent.click(await screen.findByTestId('resident-pause'));
    await waitFor(() =>
      expect(applyLifecycle).toHaveBeenCalledWith(
        expect.objectContaining({ id: makeRavn().id }),
        'suspend',
      ),
    );
    expect(await screen.findByTestId('resident-wake')).toBeInTheDocument();
    expect(screen.queryByTestId('resident-pause')).not.toBeInTheDocument();
  });

  it('puts a failed lifecycle command on the card instead of swallowing it', async () => {
    const applyLifecycle = vi.fn().mockRejectedValue(new Error('suspend refused'));
    mount(
      valkyrieServices({
        'ravn.residents': { ...makeServices()['ravn.residents'], applyLifecycle },
      }),
    );

    fireEvent.click(await screen.findByTestId('resident-pause'));
    expect(await screen.findByText('suspend refused')).toBeInTheDocument();
    expect(await screen.findByTestId('resident-pause')).toBeInTheDocument();
  });

  it('talks through the newest existing session', async () => {
    mount(
      valkyrieServices({
        'ravn.sessions': {
          listSessions: vi
            .fn()
            .mockResolvedValue([
              makeSession(),
              makeSession({
                id: '55555555-5555-4555-8555-555555555555',
                createdAt: '2026-09-14T09:00:00Z',
              }),
            ]),
          getSession: vi.fn(),
          getMessages: vi.fn(),
        },
      }),
    );
    fireEvent.click(await screen.findByTestId('resident-talk'));
    await waitFor(() =>
      expect(window.location.search).toContain('session=55555555-5555-4555-8555-555555555555'),
    );
    expect(window.location.pathname).toBe('/ravn/sessions');
    expect(localStorage.getItem('ravn.session')).toContain('55555555-5555-4555-8555-555555555555');
  });

  it('opens a session first when the resident has none', async () => {
    const createSession = vi.fn().mockResolvedValue(makeSession());
    mount(
      valkyrieServices({
        'ravn.residents': { ...makeServices()['ravn.residents'], createSession },
      }),
    );
    fireEvent.click(await screen.findByTestId('resident-talk'));
    await waitFor(() =>
      expect(createSession).toHaveBeenCalledWith(expect.objectContaining({ id: makeRavn().id }), {
        title: 'Talk with lexi-api',
      }),
    );
    await waitFor(() => expect(window.location.pathname).toBe('/ravn/sessions'));
  });

  it('cannot talk to a resident that has neither a session nor the capability', async () => {
    mount(
      valkyrieServices({
        'ravn.ravens': {
          listRavens: vi.fn().mockResolvedValue([makeRavn({ capabilities: ['chat'] })]),
          getRaven: vi.fn(),
        },
      }),
    );
    expect(await screen.findByTestId('resident-talk')).toBeDisabled();
  });

  it('lists personas with how many residents run them and opens one for editing', async () => {
    const navigated = mount(
      valkyrieServices({
        'ravn.personas': {
          listPersonas: vi
            .fn()
            .mockResolvedValue([
              makePersona(),
              makePersona({ name: 'idle-one', summary: 'unused' }),
            ]),
        },
      }),
    );
    const row = await screen.findByTestId('persona-row-realm-lexi-api');
    expect(within(row).getByText('keeps the Lexi API healthy')).toBeInTheDocument();
    expect(within(row).getByText('used by 1 resident')).toBeInTheDocument();
    expect(
      within(screen.getByTestId('persona-row-idle-one')).getByText('used by 0 residents'),
    ).toBeInTheDocument();

    fireEvent.click(within(row).getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(navigated.state.location.pathname).toBe('/ravn/personas'));
    expect(localStorage.getItem('ravn.persona')).toBe('"realm-lexi-api"');
  });

  it('reports a failure to load the residents rather than showing an empty board', async () => {
    mount(
      valkyrieServices({
        'ravn.ravens': {
          listRavens: vi.fn().mockRejectedValue(new Error('fleet unreachable')),
          getRaven: vi.fn(),
        },
      }),
    );
    expect(await screen.findByText('fleet unreachable')).toBeInTheDocument();
  });

  it('shows a loading state while the fleet is still answering', async () => {
    mount(
      valkyrieServices({
        'ravn.ravens': { listRavens: () => new Promise(() => undefined), getRaven: vi.fn() },
      }),
    );
    expect(await screen.findByLabelText('Loading residents…')).toBeInTheDocument();
  });
});
