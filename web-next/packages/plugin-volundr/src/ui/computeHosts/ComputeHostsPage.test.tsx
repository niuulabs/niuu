import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { ApiClientError } from '@niuulabs/query';
import { createMockVolundrService } from '../../adapters/mock';
import type { IVolundrService } from '../../ports/IVolundrService';
import { renderWithVolundr } from '../../testing/renderWithVolundr';
import { ComputeHostsPage } from './ComputeHostsPage';

describe('ComputeHostsPage', () => {
  it('shows the hosts of the Forge named by the page', async () => {
    renderWithVolundr(<ComputeHostsPage instanceId="mock-volundr-gpu" />);
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Mock Volundr GPU Hosts' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Platform › Forges ›/)).toHaveTextContent('Mock Volundr GPU › Compute');
    expect(screen.getByText(/Machines registered as compute for this Forge/)).toBeInTheDocument();
    expect(await screen.findByTestId('host-row-hrimfaxi')).toBeInTheDocument();
  });

  it('shows the default Forge without an instance', async () => {
    renderWithVolundr(<ComputeHostsPage />);
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Mock Volundr Hosts' }),
    ).toBeInTheDocument();
  });

  it('uses the instance id as the name of a Forge it does not know', async () => {
    renderWithVolundr(<ComputeHostsPage instanceId="forge-east" />);
    expect(
      await screen.findByRole('heading', { level: 1, name: 'forge-east Hosts' }),
    ).toBeInTheDocument();
  });

  it('says a Forge without compute hosts has none', async () => {
    const service: IVolundrService = {
      ...createMockVolundrService(),
      getComputeHosts: vi.fn().mockResolvedValue(null),
    };
    renderWithVolundr(<ComputeHostsPage instanceId="mock-volundr-default" />, { service });
    expect(
      await screen.findByText('This Forge has no compute hosts configured.'),
    ).toBeInTheDocument();
    expect(screen.queryByTestId(/^host-row-/)).toBeNull();
  });

  it('asks for a Forge when there is none to show', async () => {
    const service: IVolundrService = {
      ...createMockVolundrService(),
      getTargets: vi.fn().mockResolvedValue([]),
    };
    renderWithVolundr(<ComputeHostsPage />, { service });
    expect(
      await screen.findByText('No Forge is selected. Open a Forge from Platform to see its hosts.'),
    ).toBeInTheDocument();
  });

  it('shows the error when the Forges cannot be listed', async () => {
    const service: IVolundrService = {
      ...createMockVolundrService(),
      getTargets: vi.fn().mockRejectedValue(new ApiClientError('failed', 500, 'registry down')),
    };
    renderWithVolundr(<ComputeHostsPage />, { service });
    expect(await screen.findByRole('alert')).toHaveTextContent('registry down');
  });

  it('shows a loading state first', async () => {
    renderWithVolundr(<ComputeHostsPage />);
    expect(screen.getByText('Loading Forges…')).toBeInTheDocument();
    await screen.findByTestId('compute-hosts-page');
  });
});
