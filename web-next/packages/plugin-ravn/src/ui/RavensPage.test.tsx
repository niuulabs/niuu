import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { RavensPage } from './RavensPage';
import {
  createMockRavenStream,
  createMockBudgetStream,
  createMockTriggerStore,
  createMockSessionStream,
} from '../adapters/mock';

function makeServices(overrides?: Record<string, unknown>) {
  return {
    'ravn.ravens': createMockRavenStream(),
    'ravn.budget': createMockBudgetStream(),
    'ravn.triggers': createMockTriggerStore(),
    'ravn.sessions': createMockSessionStream(),
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

  it('renders the split fleet layout controls', async () => {
    render(<RavensPage />, { wrapper: wrap() });

    await waitFor(() => expect(screen.getByTestId('ravens-page')).toBeInTheDocument());
    expect(screen.getByTestId('ravens-sidebar')).toBeInTheDocument();
    expect(screen.getByTestId('ravens-search')).toBeInTheDocument();
    expect(screen.getByTestId('grouping-selector')).toBeInTheDocument();
    expect(screen.getByTestId('layout-split')).toBeInTheDocument();
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
