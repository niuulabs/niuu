import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ResidentFlockDeployDialog } from './ResidentFlockDeployDialog';

describe('ResidentFlockDeployDialog', () => {
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
          }}
        >
          <ResidentFlockDeployDialog open onOpenChange={vi.fn()} onDeployed={vi.fn()} />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    fireEvent.change(screen.getByTestId('flock-name'), { target: { value: 'proof' } });
    await screen.findByTestId('flock-ravn-name');
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
          }}
        >
          <ResidentFlockDeployDialog open onOpenChange={vi.fn()} onDeployed={vi.fn()} />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    fireEvent.change(screen.getByTestId('flock-name'), { target: { value: 'proof' } });
    await screen.findByTestId('flock-ravn-name');
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
          }}
        >
          <ResidentFlockDeployDialog open onOpenChange={vi.fn()} onDeployed={vi.fn()} />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('Flock deployment failed');
    expect(screen.getByTestId('flock-deploy-submit')).toBeDisabled();
  });
});
