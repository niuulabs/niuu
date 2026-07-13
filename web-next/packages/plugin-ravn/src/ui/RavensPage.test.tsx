import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { RavensPage } from './RavensPage';
import type { Ravn } from '../domain/ravn';
import {
  createMockRavenStream,
  createMockBudgetStream,
  createMockTriggerStore,
  createMockSessionStream,
  createMockPersonaStore,
} from '../adapters/mock';

function makeServices(overrides?: Record<string, unknown>) {
  return {
    'ravn.ravens': createMockRavenStream(),
    'ravn.budget': createMockBudgetStream(),
    'ravn.triggers': createMockTriggerStore(),
    'ravn.sessions': createMockSessionStream(),
    'ravn.personas': createMockPersonaStore(),
    bifrost: { listModels: vi.fn().mockResolvedValue([]) },
    'ravn.residents': {
      listProfiles: vi.fn().mockResolvedValue([]),
      deploy: vi.fn(),
      applyLifecycle: vi.fn(),
      delete: vi.fn(),
      getLogs: vi.fn(),
      listSessions: vi.fn().mockResolvedValue([]),
      createSession: vi.fn(),
      deleteSession: vi.fn(),
    },
    ...overrides,
  };
}

function wrap(services = makeServices()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

beforeEach(() => {
  localStorage.clear();
});

const TEST_FLOCK_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';

function makeFlockRaven(overrides: Partial<Ravn> = {}): Ravn {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    personaName: 'coordinator',
    residentName: 'Proof ravn',
    status: 'active',
    model: 'gpt-5.6',
    createdAt: '2026-07-13T10:00:00Z',
    managed: true,
    kind: 'resident',
    backend: 'openshell',
    engine: 'ravn',
    instanceId: 'target-a',
    instanceName: 'Alpha',
    flockId: TEST_FLOCK_ID,
    flockRole: 'coordinator',
    ...overrides,
  };
}

describe('RavensPage', () => {
  it('shows loading state initially', () => {
    const slow = { listRavens: () => new Promise(() => undefined) };
    render(<RavensPage />, { wrapper: wrap(makeServices({ 'ravn.ravens': slow })) });
    expect(screen.getByTestId('ravens-loading')).toBeInTheDocument();
  });

  it('shows error state when ravens service fails', async () => {
    const failing = { listRavens: () => Promise.reject(new Error('load failed')) };
    render(<RavensPage />, { wrapper: wrap(makeServices({ 'ravn.ravens': failing })) });
    await waitFor(() => expect(screen.getByTestId('ravens-error')).toBeInTheDocument());
    expect(screen.getByText(/load failed/i)).toBeInTheDocument();
  });

  it('uses the standard error message for non-Error failures', async () => {
    const failing = { listRavens: () => Promise.reject('disconnected') };
    render(<RavensPage />, { wrapper: wrap(makeServices({ 'ravn.ravens': failing })) });
    await waitFor(() => expect(screen.getByTestId('ravens-error')).toBeInTheDocument());
    expect(screen.getByText('Failed to load ravens')).toBeInTheDocument();
  });

  it('renders an empty fleet without a selected detail', async () => {
    const empty = { listRavens: vi.fn().mockResolvedValue([]), getRaven: vi.fn() };
    render(<RavensPage />, { wrapper: wrap(makeServices({ 'ravn.ravens': empty })) });

    await waitFor(() => expect(screen.getByTestId('ravens-page')).toBeInTheDocument());
    expect(screen.getByTestId('fleet-counts')).toHaveTextContent('0 total·0 active');
    expect(screen.getByTestId('detail-empty')).toBeInTheDocument();
  });

  it('renders the split fleet layout controls', async () => {
    render(<RavensPage />, { wrapper: wrap() });

    await waitFor(() => expect(screen.getByTestId('ravens-page')).toBeInTheDocument());
    expect(screen.getByTestId('ravens-sidebar')).toBeInTheDocument();
    expect(screen.getByTestId('ravens-search')).toBeInTheDocument();
    expect(screen.getByTestId('grouping-selector')).toBeInTheDocument();
    expect(screen.getByTestId('layout-split')).toBeInTheDocument();
  });

  it('shows a managed resident persona instead of the generic role label', async () => {
    const resident = makeFlockRaven({
      personaName: 'security-auditor',
      role: 'build',
      engine: 'hermes',
    });
    const stream = {
      listRavens: vi.fn().mockResolvedValue([resident]),
      getRaven: vi.fn().mockResolvedValue(resident),
    };

    render(<RavensPage />, {
      wrapper: wrap(makeServices({ 'ravn.ravens': stream })),
    });

    const row = await screen.findByTestId('ravn-list-row');
    expect(within(row).getByText('security auditor · hermes')).toBeInTheDocument();
  });

  it('selects a ravn by default and shows its detail pane', async () => {
    render(<RavensPage />, { wrapper: wrap() });

    await waitFor(() => expect(screen.getByTestId('ravn-detail')).toBeInTheDocument());
    expect(screen.getAllByText(/sindri/i).length).toBeGreaterThan(0);
  });

  it('pins the displayed resident across refreshed list ordering', async () => {
    const first = {
      id: '11111111-1111-4111-8111-111111111111',
      personaName: 'coder',
      residentName: 'First resident',
      status: 'active' as const,
      model: 'qwen3.5',
      createdAt: '2026-07-11T20:00:00Z',
      managed: true,
      kind: 'resident' as const,
      backend: 'openshell' as const,
      engine: 'hermes' as const,
      desiredState: 'running' as const,
      observedState: 'active' as const,
      capabilities: ['runtime.restart' as const],
      instanceId: 'target-a',
      instanceName: 'Alpha',
    };
    const second = {
      ...first,
      id: '22222222-2222-4222-8222-222222222222',
      residentName: 'Second resident',
    };
    const listRavens = vi
      .fn()
      .mockResolvedValueOnce([first, second])
      .mockResolvedValue([second, first]);
    const applyLifecycle = vi.fn().mockResolvedValue(first);
    const services = makeServices({
      'ravn.ravens': { listRavens, getRaven: vi.fn() },
      'ravn.residents': {
        ...makeServices()['ravn.residents'],
        applyLifecycle,
      },
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const Wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );

    render(<RavensPage />, { wrapper: Wrapper });

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'First resident' })).toBeVisible(),
    );
    await client.invalidateQueries({ queryKey: ['ravn', 'ravens'] });
    await waitFor(() => expect(listRavens).toHaveBeenCalledTimes(2));
    expect(screen.getByRole('heading', { name: 'First resident' })).toBeVisible();

    fireEvent.click(screen.getByTestId('resident-restart'));
    await waitFor(() => expect(applyLifecycle).toHaveBeenCalledWith(first, 'restart'));
  });

  it('filters the fleet list from the left rail search', async () => {
    render(<RavensPage />, { wrapper: wrap() });

    await waitFor(() => expect(screen.getAllByTestId('ravn-list-row').length).toBeGreaterThan(1));
    fireEvent.change(screen.getByTestId('ravens-search'), { target: { value: 'muninn' } });

    await waitFor(() => expect(screen.getAllByTestId('ravn-list-row')).toHaveLength(1));
    expect(screen.getByText('muninn')).toBeInTheDocument();
  });

  it('switches grouping from the segmented control and persists it', async () => {
    render(<RavensPage />, { wrapper: wrap() });

    await waitFor(() => expect(screen.getByTestId('group-btn-state')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('group-btn-state'));

    await waitFor(() => expect(screen.getByText('Active')).toBeInTheDocument());
    expect(localStorage.getItem('ravn.ravens.group')).toBe('"state"');
  });

  it('renders fallback metadata, mixed session ownership, failure counts, and flat grouping', async () => {
    const unnamed = makeFlockRaven({
      id: '77777777-7777-4777-8777-777777777777',
      personaName: '',
      residentName: undefined,
      role: 'plan',
      engine: undefined,
      deployment: 'helm_release',
      instanceId: undefined,
      instanceName: undefined,
      location: 'test_zone',
      flockId: undefined,
      flockRole: undefined,
    });
    const failed = makeFlockRaven({
      id: '88888888-8888-4888-8888-888888888888',
      residentName: 'Fallback resident',
      status: 'failed',
      engine: undefined,
      deployment: undefined,
      instanceName: undefined,
      location: undefined,
      flockId: undefined,
      flockRole: undefined,
    });
    const sessions = {
      ...createMockSessionStream(),
      listSessions: vi.fn().mockResolvedValue([
        {
          id: '99999999-9999-4999-8999-999999999991',
          ravnId: unnamed.id,
          personaName: 'planner',
          status: 'running',
          model: 'gpt-5.6',
          createdAt: '2026-07-13T10:01:00Z',
        },
        {
          id: '99999999-9999-4999-8999-999999999992',
          ravnId: failed.id,
          instanceId: 'target-a',
          personaName: 'coder',
          status: 'running',
          model: 'gpt-5.6',
          createdAt: '2026-07-13T10:02:00Z',
        },
      ]),
    };
    render(<RavensPage />, {
      wrapper: wrap(
        makeServices({
          'ravn.ravens': {
            listRavens: vi.fn().mockResolvedValue([unnamed, failed]),
            getRaven: vi.fn(),
          },
          'ravn.sessions': sessions,
        }),
      ),
    });

    await waitFor(() => expect(screen.getAllByTestId('ravn-list-row')).toHaveLength(2));
    expect(screen.getByTestId('fleet-counts')).toHaveTextContent('2 total·1 active·1 failed');
    const unnamedRow = screen.getAllByTestId('ravn-list-row')[0]!;
    expect(unnamedRow).toHaveTextContent('77777777');
    expect(unnamedRow).toHaveTextContent('planner · helm release');
    expect(unnamedRow).toHaveTextContent('test zone');
    await waitFor(() => expect(unnamedRow).toHaveTextContent('1 sess'));
    expect(screen.getAllByTestId('ravn-list-row')[1]).toHaveTextContent('coordinator · ravn');
    expect(screen.getAllByTestId('ravn-list-row')[1]).toHaveTextContent('unknown');

    fireEvent.change(screen.getByTestId('ravens-search'), { target: { value: 'not-present' } });
    expect(screen.getByText(/no ravens match/i)).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('ravens-search'), { target: { value: '' } });
    fireEvent.click(screen.getByTestId('group-btn-none'));
    expect(document.querySelector('.rv-group-header')).not.toBeInTheDocument();
  });

  it('deletes every resident in a flock from one group action', async () => {
    const flockRavens = [
      makeFlockRaven(),
      makeFlockRaven({
        id: '22222222-2222-4222-8222-222222222222',
        personaName: 'specialist',
        residentName: 'Proof hermes',
        model: 'nemotron',
        engine: 'hermes',
        flockRole: 'specialist',
      }),
    ];
    const independent = makeFlockRaven({
      id: '33333333-3333-4333-8333-333333333333',
      residentName: 'Independent ravn',
      flockId: undefined,
      flockRole: undefined,
    });
    const remove = vi.fn().mockResolvedValue(undefined);
    render(<RavensPage />, {
      wrapper: wrap(
        makeServices({
          'ravn.ravens': {
            listRavens: vi.fn().mockResolvedValue([...flockRavens, independent]),
            getRaven: vi.fn(),
          },
          'ravn.residents': {
            ...makeServices()['ravn.residents'],
            delete: remove,
          },
        }),
      ),
    });

    await waitFor(() => expect(screen.getByTestId('group-btn-flock')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('ravens-search'), { target: { value: 'Proof ravn' } });
    expect(screen.getAllByTestId('ravn-list-row')).toHaveLength(1);
    fireEvent.click(screen.getByTestId('group-btn-flock'));
    expect(screen.queryByRole('button', { name: 'Delete Independent' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Delete Flock Aaaaaaaa' }));
    expect(screen.getByText(/all 2 residents/i)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('flock-delete-confirm'));

    await waitFor(() => expect(remove).toHaveBeenCalledTimes(2));
    expect(remove).toHaveBeenCalledWith(flockRavens[0]);
    expect(remove).toHaveBeenCalledWith(flockRavens[1]);
    expect(remove).not.toHaveBeenCalledWith(independent);
  });

  it('keeps flock deletion failures visible', async () => {
    const flockRaven = makeFlockRaven();
    render(<RavensPage />, {
      wrapper: wrap(
        makeServices({
          'ravn.ravens': {
            listRavens: vi.fn().mockResolvedValue([flockRaven]),
            getRaven: vi.fn(),
          },
          'ravn.residents': {
            ...makeServices()['ravn.residents'],
            delete: vi.fn().mockRejectedValue(new Error('gateway unavailable')),
          },
        }),
      ),
    });

    await waitFor(() => expect(screen.getByTestId('group-btn-flock')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('group-btn-flock'));
    fireEvent.click(screen.getByRole('button', { name: 'Delete Flock Aaaaaaaa' }));
    fireEvent.click(screen.getByTestId('flock-delete-confirm'));

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Failed to delete 1 of 1 flock members'),
    );
    expect(screen.getByTestId('flock-delete-confirm')).toBeInTheDocument();
  });

  it('supports closing and cancelling flock deletion without deleting', async () => {
    const remove = vi.fn();
    render(<RavensPage />, {
      wrapper: wrap(
        makeServices({
          'ravn.ravens': {
            listRavens: vi.fn().mockResolvedValue([makeFlockRaven()]),
            getRaven: vi.fn(),
          },
          'ravn.residents': {
            ...makeServices()['ravn.residents'],
            delete: remove,
          },
        }),
      ),
    });

    await waitFor(() => expect(screen.getByTestId('group-btn-flock')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('group-btn-flock'));
    fireEvent.click(screen.getByRole('button', { name: 'Delete Flock Aaaaaaaa' }));
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByText('Delete flock')).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Delete Flock Aaaaaaaa' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('Delete flock')).not.toBeInTheDocument());
    expect(remove).not.toHaveBeenCalled();
  });

  it('locks flock deletion controls while all members are being removed', async () => {
    let finishDelete: (() => void) | undefined;
    const remove = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishDelete = resolve;
        }),
    );
    render(<RavensPage />, {
      wrapper: wrap(
        makeServices({
          'ravn.ravens': {
            listRavens: vi.fn().mockResolvedValue([makeFlockRaven()]),
            getRaven: vi.fn(),
          },
          'ravn.residents': {
            ...makeServices()['ravn.residents'],
            delete: remove,
          },
        }),
      ),
    });

    await waitFor(() => expect(screen.getByTestId('group-btn-flock')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('group-btn-flock'));
    fireEvent.click(screen.getByRole('button', { name: 'Delete Flock Aaaaaaaa' }));
    fireEvent.click(screen.getByTestId('flock-delete-confirm'));

    await waitFor(() => expect(screen.getByText('Deleting…')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    expect(screen.getByTestId('flock-delete-confirm')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.getByText('Delete flock')).toBeInTheDocument();

    finishDelete?.();
    await waitFor(() => expect(screen.queryByText('Delete flock')).not.toBeInTheDocument());
  });

  it('selects and groups the coordinator after deploying a flock', async () => {
    const profiles = [
      {
        id: 'ravn-local',
        displayName: 'Ravn',
        description: 'Ravn resident',
        backend: 'local' as const,
        engine: 'ravn' as const,
        capabilities: ['chat' as const, 'flock' as const],
        defaultModel: 'niuu/qwen',
        allowedModels: [],
        labels: [],
        instanceId: 'local',
        instanceName: 'Local',
        instanceSlug: 'local',
      },
      {
        id: 'hermes-local',
        displayName: 'NemoHermes',
        description: 'Hermes resident',
        backend: 'local' as const,
        engine: 'hermes' as const,
        capabilities: ['chat' as const, 'flock' as const],
        defaultModel: 'niuu/qwen',
        allowedModels: [],
        labels: [],
        instanceId: 'local',
        instanceName: 'Local',
        instanceSlug: 'local',
      },
    ];
    const deploy = vi.fn().mockImplementation(async (request) =>
      makeFlockRaven({
        id: crypto.randomUUID(),
        residentName: request.name,
        profileId: request.profileId,
        instanceId: request.instanceId,
        flockId: request.flockId,
        flockRole: request.flockRole,
      }),
    );
    render(<RavensPage />, {
      wrapper: wrap(
        makeServices({
          'ravn.residents': {
            ...makeServices()['ravn.residents'],
            listProfiles: vi.fn().mockResolvedValue(profiles),
            deploy,
          },
          'ravn.personas': {
            listPersonas: vi.fn().mockResolvedValue([
              {
                name: 'reviewer',
                role: 'review',
                letter: 'R',
                color: 'ice',
                summary: 'Reviews subscribed changes',
                permissionMode: 'full-access',
                allowedTools: [],
                iterationBudget: 40,
                isBuiltin: true,
                hasOverride: false,
                producesEvent: 'review.completed',
                consumesEvents: ['code.changed'],
              },
            ]),
          },
        }),
      ),
    });

    await waitFor(() => expect(screen.getByTestId('flock-deploy-open')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('flock-deploy-open'));
    fireEvent.change(screen.getByTestId('flock-name'), { target: { value: 'fleet proof' } });
    await screen.findByTestId('flock-member-0-name');
    fireEvent.click(screen.getByTestId('flock-add-member'));
    fireEvent.change(screen.getByTestId('flock-member-0-persona'), {
      target: { value: 'reviewer' },
    });
    fireEvent.change(screen.getByTestId('flock-member-1-persona'), {
      target: { value: 'reviewer' },
    });
    fireEvent.click(screen.getByTestId('flock-deploy-submit'));

    await waitFor(() => expect(deploy).toHaveBeenCalledTimes(2));
    expect(localStorage.getItem('ravn.ravens.group')).toBe('"flock"');
  });

  it('switches the selected ravn when a different list row is clicked', async () => {
    render(<RavensPage />, { wrapper: wrap() });

    await waitFor(() => expect(screen.getAllByTestId('ravn-list-row').length).toBeGreaterThan(1));
    const muninnRow = screen
      .getAllByTestId('ravn-list-row')
      .find((row) => within(row).queryByText('muninn'));

    expect(muninnRow).toBeTruthy();
    if (muninnRow) fireEvent.click(muninnRow);

    await waitFor(() => expect(screen.getAllByText('muninn').length).toBeGreaterThan(0));
  });

  it('collapses and expands the fleet sidebar', async () => {
    render(<RavensPage />, { wrapper: wrap() });

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /collapse ravens sidebar/i })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /collapse ravens sidebar/i }));
    expect(screen.getByRole('button', { name: /expand ravens sidebar/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /expand ravens sidebar/i }));
    expect(screen.getByRole('button', { name: /collapse ravens sidebar/i })).toBeInTheDocument();
  });

  it('filters profiles by target and deploys through the resident control port', async () => {
    const profiles = [
      {
        id: 'ravn-helm',
        displayName: 'Ravn Helm',
        description: 'Helm resident',
        backend: 'helmrelease' as const,
        engine: 'ravn' as const,
        capabilities: ['chat' as const],
        defaultModel: 'gpt-5.6',
        allowedModels: ['gpt-5.6'],
        labels: [],
        instanceId: 'target-a',
        instanceName: 'Alpha',
        instanceSlug: 'alpha',
      },
      {
        id: 'nemohermes-openshell',
        displayName: 'NemoHermes',
        description: 'Hermes resident',
        backend: 'openshell' as const,
        engine: 'hermes' as const,
        capabilities: ['chat' as const, 'session.create' as const],
        defaultModel: 'qwen3.5',
        allowedModels: ['qwen3.5'],
        labels: [],
        instanceId: 'target-b',
        instanceName: 'Beta',
        instanceSlug: 'beta',
      },
    ];
    const deployed = {
      id: '99999999-9999-4999-8999-999999999999',
      personaName: 'product-steward',
      status: 'idle' as const,
      model: 'qwen3.5',
      createdAt: '2026-07-11T20:00:00Z',
      managed: true,
      instanceId: 'target-b',
    };
    const deploy = vi.fn().mockResolvedValue(deployed);
    const residentControl = {
      ...makeServices()['ravn.residents'],
      listProfiles: vi.fn().mockResolvedValue(profiles),
      deploy,
    };
    render(<RavensPage />, {
      wrapper: wrap(makeServices({ 'ravn.residents': residentControl })),
    });

    await waitFor(() => expect(screen.getByTestId('resident-deploy-open')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('resident-deploy-open'));
    await waitFor(() => expect(screen.getByTestId('resident-target')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('resident-target'), { target: { value: 'target-b' } });
    expect(screen.getByTestId('resident-profile')).toHaveValue('nemohermes-openshell');
    expect(screen.queryByRole('option', { name: 'Ravn Helm' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByTestId('resident-name'), { target: { value: 'Sol' } });
    fireEvent.change(screen.getByTestId('resident-persona'), {
      target: { value: 'product-steward' },
    });
    fireEvent.click(screen.getByTestId('resident-deploy-submit'));

    await waitFor(() =>
      expect(deploy).toHaveBeenCalledWith({
        name: 'Sol',
        profileId: 'nemohermes-openshell',
        instanceId: 'target-b',
        personaName: 'product-steward',
        model: 'qwen3.5',
      }),
    );
  });

  it('keeps real deployment failures visible in the dialog', async () => {
    const profile = {
      id: 'ravn-openshell',
      displayName: 'Ravn OpenShell',
      description: 'OpenShell resident',
      backend: 'openshell' as const,
      engine: 'ravn' as const,
      capabilities: ['chat' as const],
      defaultModel: 'gpt-5.6',
      allowedModels: ['gpt-5.6'],
      labels: [],
      instanceId: 'target-a',
      instanceName: 'Alpha',
      instanceSlug: 'alpha',
    };
    const residentControl = {
      ...makeServices()['ravn.residents'],
      listProfiles: vi.fn().mockResolvedValue([profile]),
      deploy: vi.fn().mockRejectedValue(new Error('OpenShell gateway unavailable')),
    };
    render(<RavensPage />, {
      wrapper: wrap(makeServices({ 'ravn.residents': residentControl })),
    });

    await waitFor(() => expect(screen.getByTestId('resident-deploy-open')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('resident-deploy-open'));
    await waitFor(() => expect(screen.getByTestId('resident-name')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('resident-name'), { target: { value: 'Sol' } });
    fireEvent.click(screen.getByTestId('resident-deploy-submit'));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('gateway unavailable'));
    expect(screen.getByTestId('resident-name')).toBeInTheDocument();
  });

  it('keeps same-id residents distinct by their opaque owning target', async () => {
    const sharedId = '88888888-8888-4888-8888-888888888888';
    const ravens = [
      {
        id: sharedId,
        personaName: 'steward-a',
        residentName: 'Alpha resident',
        status: 'active' as const,
        model: 'qwen3.5',
        createdAt: '2026-07-11T20:00:00Z',
        managed: true,
        kind: 'resident' as const,
        backend: 'openshell' as const,
        engine: 'hermes' as const,
        instanceId: 'target-a',
        instanceName: 'Alpha',
      },
      {
        id: sharedId,
        personaName: 'steward-b',
        residentName: 'Beta resident',
        status: 'active' as const,
        model: 'qwen3.5',
        createdAt: '2026-07-11T20:01:00Z',
        managed: true,
        kind: 'resident' as const,
        backend: 'openshell' as const,
        engine: 'hermes' as const,
        instanceId: 'target-b',
        instanceName: 'Beta',
      },
    ];
    const ravenStream = {
      listRavens: vi.fn().mockResolvedValue(ravens),
      getRaven: vi.fn(),
    };
    render(<RavensPage />, {
      wrapper: wrap(makeServices({ 'ravn.ravens': ravenStream })),
    });

    await waitFor(() => expect(screen.getAllByTestId('ravn-list-row')).toHaveLength(2));
    const betaRow = screen
      .getAllByTestId('ravn-list-row')
      .find((row) => within(row).queryByText('Beta resident'));
    expect(betaRow).toBeTruthy();
    if (betaRow) fireEvent.click(betaRow);

    await waitFor(() => expect(screen.getAllByText('Beta').length).toBeGreaterThan(0));
    expect(screen.getByTestId('runtime-panel')).toHaveTextContent('targetBeta');
  });
});
