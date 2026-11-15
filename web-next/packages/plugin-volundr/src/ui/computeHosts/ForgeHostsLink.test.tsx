import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApiClientError } from '@niuulabs/query';
import { createMockVolundrService } from '../../adapters/mock';
import type { IVolundrService } from '../../ports/IVolundrService';
import { renderWithVolundr } from '../../testing/renderWithVolundr';
import { ForgeHostsLink, computeHostsHref } from './ForgeHostsLink';

describe('computeHostsHref', () => {
  it('names the Forge and keeps the rest of the page search', () => {
    expect(computeHostsHref('forge/a', '?config=%2Fconfig.live.json&register=1')).toBe(
      '/volundr/hosts?config=%2Fconfig.live.json&instance_id=forge%2Fa',
    );
    expect(computeHostsHref('f', '')).toBe('/volundr/hosts?instance_id=f');
  });
});

describe('ForgeHostsLink', () => {
  it('links to the Hosts page of a Forge that has compute hosts', async () => {
    renderWithVolundr(<ForgeHostsLink instanceId="forge-a" className="action" />);
    const link = await screen.findByRole('link', { name: 'Compute hosts' });
    expect(link).toHaveAttribute('href', '/volundr/hosts?instance_id=forge-a');
    expect(link).toHaveClass('action');
  });

  it('as a chip, carries the host count and the free slots', async () => {
    renderWithVolundr(<ForgeHostsLink instanceId="forge-a" variant="chip" className="chip" />);
    const link = await screen.findByRole('link', { name: /^Hosts: \d+ hosts?, \d+ free slots?$/ });
    expect(link).toHaveAttribute('href', '/volundr/hosts?instance_id=forge-a');
    expect(link).toHaveClass('chip');
    expect(link).toHaveTextContent(/^Hosts · 8 · 8 free$/);
  });

  it('as a chip, reads one host and one slot in the singular', async () => {
    const getComputeHosts = vi.fn().mockResolvedValue({
      summary: { hosts: 1, slotsFree: 1 },
    });
    const service: IVolundrService = { ...createMockVolundrService(), getComputeHosts };
    renderWithVolundr(<ForgeHostsLink instanceId="forge-a" variant="chip" />, { service });
    expect(await screen.findByRole('link', { name: 'Hosts: 1 host, 1 free slot' })).toBeVisible();
  });

  it('does not appear for a Forge without compute hosts', async () => {
    const getComputeHosts = vi.fn().mockResolvedValue(null);
    const service: IVolundrService = { ...createMockVolundrService(), getComputeHosts };
    renderWithVolundr(<ForgeHostsLink instanceId="forge-a" />, { service });
    await waitFor(() => expect(getComputeHosts).toHaveBeenCalled());
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('does not appear when the person may not read the hosts', async () => {
    const getComputeHosts = vi.fn().mockRejectedValue(new ApiClientError('no', 403, 'forbidden'));
    const service: IVolundrService = { ...createMockVolundrService(), getComputeHosts };
    renderWithVolundr(<ForgeHostsLink instanceId="forge-a" />, { service });
    await waitFor(() => expect(getComputeHosts).toHaveBeenCalled());
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('renders nothing where there is no Forge service', () => {
    const client = new QueryClient();
    const { container } = render(
      <QueryClientProvider client={client}>
        <ForgeHostsLink instanceId="forge-a" />
      </QueryClientProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
