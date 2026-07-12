import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ResidentDeployDialog } from './ResidentDeployDialog';

describe('ResidentDeployDialog', () => {
  it('deploys with a persona selected from the shared catalog', async () => {
    const deploy = vi.fn().mockResolvedValue({ id: 'ravn-1' });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            'ravn.residents': {
              listProfiles: vi.fn().mockResolvedValue([
                {
                  id: 'nemohermes-local',
                  displayName: 'NemoHermes',
                  description: 'Resident Hermes runtime',
                  backend: 'local',
                  engine: 'hermes',
                  instanceId: 'local',
                  instanceName: 'Local',
                  allowedModels: [],
                  defaultModel: '',
                },
              ]),
              deploy,
            },
            'ravn.personas': {
              listPersonas: vi.fn().mockResolvedValue([{ name: 'reviewer', role: 'review' }]),
            },
          }}
        >
          <ResidentDeployDialog open onOpenChange={vi.fn()} onDeployed={vi.fn()} />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    fireEvent.change(await screen.findByTestId('resident-name'), {
      target: { value: 'resident-reviewer' },
    });
    fireEvent.change(screen.getByTestId('resident-persona'), {
      target: { value: 'reviewer' },
    });
    fireEvent.click(screen.getByTestId('resident-deploy-submit'));

    await waitFor(() =>
      expect(deploy).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'resident-reviewer', personaName: 'reviewer' }),
      ),
    );
  });
});
