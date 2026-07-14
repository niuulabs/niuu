import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ResidentFlockDeployDialog } from './ResidentFlockDeployDialog';

describe('ResidentFlockDeployDialog', () => {
  const eventPersona = {
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
  };

  const personaService = {
    listPersonas: vi.fn().mockResolvedValue([eventPersona]),
  };

  function selectEventPersona(memberCount: number) {
    for (let index = 0; index < memberCount; index += 1) {
      fireEvent.change(screen.getByTestId(`flock-member-${index}-persona`), {
        target: { value: eventPersona.name },
      });
    }
  }

  it('deploys three profiles with one shared flock identity', async () => {
    const deploy = vi.fn().mockImplementation(async (request) => ({
      id: crypto.randomUUID(),
      status: 'active',
      createdAt: new Date().toISOString(),
      ...request,
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const profiles = [
      ['ravn', 'Ravn'],
      ['openclaw', 'NemoClaw'],
      ['hermes', 'NemoHermes'],
    ].map(([engine, displayName]) => ({
      id: `${engine}-local`,
      displayName,
      description: `${displayName} resident`,
      backend: 'local',
      engine,
      capabilities: engine === 'ravn' ? ['chat', 'flock'] : ['chat'],
      instanceId: 'local',
      instanceName: 'Local',
      instanceSlug: 'local',
      allowedModels: [],
      defaultModel: 'niuu/qwen',
      labels: [],
    }));

    render(
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            'ravn.residents': { listProfiles: vi.fn().mockResolvedValue(profiles), deploy },
            'ravn.personas': personaService,
          }}
        >
          <ResidentFlockDeployDialog open onOpenChange={vi.fn()} onDeployed={vi.fn()} />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    expect(screen.getByRole('heading', { name: 'Deploy mesh' })).toBeInTheDocument();
    expect(screen.getByText('Mesh name')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('flock-name'), { target: { value: 'proof' } });
    await screen.findByTestId('flock-member-0-name');
    fireEvent.click(screen.getByTestId('flock-add-member'));
    fireEvent.click(screen.getByTestId('flock-add-member'));
    selectEventPersona(3);
    fireEvent.click(screen.getByTestId('flock-deploy-submit'));

    await waitFor(() => expect(deploy).toHaveBeenCalledTimes(3));
    const requests = deploy.mock.calls.map(([request]) => request);
    expect(new Set(requests.map((request) => request.flockId)).size).toBe(1);
    expect(new Set(requests.map((request) => request.flockMemberId)).size).toBe(3);
    expect(requests.map((request) => request.flockRole)).toEqual([
      'coordinator',
      'specialist',
      'specialist',
    ]);
    expect(requests.map((request) => request.profileId)).toEqual([
      'ravn-local',
      'openclaw-local',
      'hermes-local',
    ]);
    expect(requests.map((request) => request.personaName)).toEqual([
      'reviewer',
      'reviewer',
      'reviewer',
    ]);
  });

  it('rolls back members when one deployment fails', async () => {
    const deployed = {
      id: crypto.randomUUID(),
      name: 'proof-ravn',
      instanceId: 'local',
      profileId: 'ravn-local',
      status: 'active',
      createdAt: new Date().toISOString(),
    };
    const deploy = vi
      .fn()
      .mockResolvedValueOnce(deployed)
      .mockRejectedValueOnce(new Error('NemoClaw unavailable'))
      .mockResolvedValueOnce({ ...deployed, id: crypto.randomUUID(), name: 'proof-hermes' });
    const remove = vi.fn().mockResolvedValue(undefined);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const profiles = [
      ['ravn', 'Ravn'],
      ['openclaw', 'NemoClaw'],
      ['hermes', 'NemoHermes'],
    ].map(([engine, displayName]) => ({
      id: `${engine}-local`,
      displayName,
      description: `${displayName} resident`,
      backend: 'local',
      engine,
      capabilities: ['chat', 'flock'],
      instanceId: 'local',
      instanceName: 'Local',
      instanceSlug: 'local',
      allowedModels: [],
      defaultModel: 'niuu/qwen',
      labels: [],
    }));

    render(
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            'ravn.residents': {
              listProfiles: vi.fn().mockResolvedValue(profiles),
              deploy,
              delete: remove,
            },
            'ravn.personas': personaService,
          }}
        >
          <ResidentFlockDeployDialog open onOpenChange={vi.fn()} onDeployed={vi.fn()} />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    fireEvent.change(screen.getByTestId('flock-name'), { target: { value: 'proof' } });
    await screen.findByTestId('flock-member-0-name');
    fireEvent.click(screen.getByTestId('flock-add-member'));
    fireEvent.click(screen.getByTestId('flock-add-member'));
    selectEventPersona(3);
    fireEvent.click(screen.getByTestId('flock-deploy-submit'));

    expect(await screen.findByRole('alert')).toHaveTextContent('NemoClaw unavailable');
    expect(remove).toHaveBeenCalledTimes(2);
  });

  it('reports profile loading failures', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            'ravn.residents': {
              listProfiles: vi.fn().mockRejectedValue('unavailable'),
              deploy: vi.fn(),
            },
            'ravn.personas': personaService,
          }}
        >
          <ResidentFlockDeployDialog open onOpenChange={vi.fn()} onDeployed={vi.fn()} />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Mesh deployment failed')).toBeInTheDocument();
    expect(screen.getByTestId('flock-deploy-submit')).toBeDisabled();
  });

  it('supports an arbitrary number of configured members', async () => {
    const deploy = vi.fn().mockImplementation(async (request) => ({
      id: crypto.randomUUID(),
      status: 'active',
      createdAt: new Date().toISOString(),
      ...request,
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const profiles = [
      ['ravn', 'Ravn'],
      ['openclaw', 'NemoClaw'],
      ['hermes', 'NemoHermes'],
    ].map(([engine, displayName]) => ({
      id: `${engine}-local`,
      displayName,
      description: `${displayName} resident`,
      backend: 'local',
      engine,
      capabilities: ['chat', 'flock'],
      instanceId: 'local',
      instanceName: 'Local',
      instanceSlug: 'local',
      allowedModels: [],
      defaultModel: 'niuu/qwen',
      labels: [],
    }));

    render(
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            'ravn.residents': { listProfiles: vi.fn().mockResolvedValue(profiles), deploy },
            'ravn.personas': personaService,
          }}
        >
          <ResidentFlockDeployDialog open onOpenChange={vi.fn()} onDeployed={vi.fn()} />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    fireEvent.change(screen.getByTestId('flock-name'), { target: { value: 'five' } });
    await screen.findByTestId('flock-member-0-name');
    for (let index = 0; index < 4; index += 1) {
      fireEvent.click(screen.getByTestId('flock-add-member'));
    }
    selectEventPersona(5);
    fireEvent.click(screen.getByTestId('flock-deploy-submit'));

    await waitFor(() => expect(deploy).toHaveBeenCalledTimes(5));
    expect(screen.getAllByRole('group')).toHaveLength(5);
  });

  it('requires exactly one flock-capable coordinator', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const profiles = [
      {
        id: 'ravn-local',
        displayName: 'Ravn',
        description: 'Ravn resident',
        backend: 'local',
        engine: 'ravn',
        capabilities: ['chat', 'flock'],
        instanceId: 'local',
        instanceName: 'Local',
        instanceSlug: 'local',
        allowedModels: [],
        defaultModel: 'niuu/qwen',
        labels: [],
      },
    ];

    render(
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            'ravn.residents': {
              listProfiles: vi.fn().mockResolvedValue(profiles),
              deploy: vi.fn(),
            },
            'ravn.personas': personaService,
          }}
        >
          <ResidentFlockDeployDialog open onOpenChange={vi.fn()} onDeployed={vi.fn()} />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    fireEvent.change(screen.getByTestId('flock-name'), { target: { value: 'invalid' } });
    await screen.findByTestId('flock-member-0-name');
    fireEvent.click(screen.getByTestId('flock-add-member'));
    fireEvent.change(screen.getByTestId('flock-member-1-role'), {
      target: { value: 'coordinator' },
    });

    expect(screen.getByRole('alert')).toHaveTextContent('exactly one coordinator');
    expect(screen.getByTestId('flock-deploy-submit')).toBeDisabled();
  });

  it('requires unique member names', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const profiles = [
      {
        id: 'ravn-local',
        displayName: 'Ravn',
        description: 'Ravn resident',
        backend: 'local',
        engine: 'ravn',
        capabilities: ['chat', 'flock'],
        instanceId: 'local',
        instanceName: 'Local',
        instanceSlug: 'local',
        allowedModels: [],
        defaultModel: 'niuu/qwen',
        labels: [],
      },
    ];

    render(
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            'ravn.residents': {
              listProfiles: vi.fn().mockResolvedValue(profiles),
              deploy: vi.fn(),
            },
            'ravn.personas': personaService,
          }}
        >
          <ResidentFlockDeployDialog open onOpenChange={vi.fn()} onDeployed={vi.fn()} />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    fireEvent.change(screen.getByTestId('flock-name'), { target: { value: 'duplicate' } });
    await screen.findByTestId('flock-member-0-name');
    fireEvent.click(screen.getByTestId('flock-add-member'));
    const coordinatorName = (screen.getByTestId('flock-member-0-name') as HTMLInputElement).value;
    fireEvent.change(screen.getByTestId('flock-member-1-name'), {
      target: { value: coordinatorName },
    });

    expect(screen.getByRole('alert')).toHaveTextContent('Member names must be unique');
    expect(screen.getByTestId('flock-deploy-submit')).toBeDisabled();
  });

  it('only offers personas with input and output event contracts', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const profiles = [
      {
        id: 'ravn-local',
        displayName: 'Ravn',
        description: 'Ravn resident',
        backend: 'local',
        engine: 'ravn',
        capabilities: ['chat', 'flock'],
        instanceId: 'local',
        instanceName: 'Local',
        instanceSlug: 'local',
        allowedModels: [],
        defaultModel: 'niuu/qwen',
        labels: [],
      },
    ];
    const listPersonas = vi.fn().mockResolvedValue([
      eventPersona,
      {
        ...eventPersona,
        name: 'chat-only',
        producesEvent: '',
        consumesEvents: [],
      },
    ]);

    render(
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            'ravn.residents': {
              listProfiles: vi.fn().mockResolvedValue(profiles),
              deploy: vi.fn(),
            },
            'ravn.personas': { listPersonas },
          }}
        >
          <ResidentFlockDeployDialog open onOpenChange={vi.fn()} onDeployed={vi.fn()} />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    await screen.findByTestId('flock-member-0-persona');
    expect(screen.getByRole('option', { name: 'reviewer · review' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'chat-only · review' })).not.toBeInTheDocument();
    expect(screen.getByTestId('flock-deploy-submit')).toBeDisabled();
  });
});
