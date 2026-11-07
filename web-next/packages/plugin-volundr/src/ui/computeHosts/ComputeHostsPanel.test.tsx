import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { ApiClientError } from '@niuulabs/query';
import {
  createMockComputeHosts,
  seedComputeHosts,
  type MockComputeHostMethods,
} from '../../adapters/computeHosts.mock';
import { createMockVolundrService } from '../../adapters/mock';
import type { ComputeHost } from '../../domain/computeHosts';
import type { IVolundrService } from '../../ports/IVolundrService';
import { renderWithVolundr } from '../../testing/renderWithVolundr';
import { ComputeHostsPanel } from './ComputeHostsPanel';

const seed = seedComputeHosts();
const [hrimfaxi, skinfaxi, , , , , glad] = seed as [
  ComputeHost,
  ComputeHost,
  ComputeHost,
  ComputeHost,
  ComputeHost,
  ComputeHost,
  ComputeHost,
];

function setup(
  options: {
    hosts?: ComputeHost[];
    unavailable?: boolean;
    override?: Partial<MockComputeHostMethods>;
  } = {},
) {
  const hosts = createMockComputeHosts({ hosts: options.hosts, unavailable: options.unavailable });
  const methods: MockComputeHostMethods = { ...hosts, ...options.override };
  const spies = Object.fromEntries(
    Object.entries(methods).map(([name, fn]) => [name, vi.fn(fn as never)]),
  ) as unknown as { [K in keyof MockComputeHostMethods]: ReturnType<typeof vi.fn> };
  const service: IVolundrService = { ...createMockVolundrService(), ...spies };
  const view = renderWithVolundr(<ComputeHostsPanel instanceId="forge" />, { service });
  return { spies, view };
}

function row(name: string) {
  return screen.findByTestId(`host-row-${name}`);
}

describe('ComputeHostsPanel', () => {
  it('shows the summary and a row for every host', async () => {
    setup();
    expect(await row('hrimfaxi')).toBeInTheDocument();
    expect(
      screen.getByText(
        '3 in service · 1 draining · 1 joining · 1 out of service · 2 need attention',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('of 52')).toBeInTheDocument();
    expect(screen.getAllByTestId(/^host-row-/)).toHaveLength(8);
    expect(screen.getByText('in use')).toBeInTheDocument();
  });

  it('renders nothing for a Forge without compute hosts', async () => {
    const { spies, view } = setup({ unavailable: true });
    await waitFor(() => expect(spies.getComputeHosts).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText('Loading hosts…')).toBeNull());
    expect(view.container).toBeEmptyDOMElement();
  });

  it('shows the error when the hosts cannot be read, and retries', async () => {
    const getComputeHosts = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError('failed', 403, 'volundr:admin required'))
      .mockImplementation(createMockComputeHosts().getComputeHosts);
    setup({ override: { getComputeHosts } });
    expect(await screen.findByRole('alert')).toHaveTextContent('volundr:admin required');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await row('hrimfaxi')).toBeInTheDocument();
  });

  it('shows a loading state first', async () => {
    setup();
    expect(screen.getByText('Loading hosts…')).toBeInTheDocument();
    await row('hrimfaxi');
  });

  describe('tag filter', () => {
    it('shows only the hosts that carry every chosen tag', async () => {
      setup();
      await row('hrimfaxi');
      const input = screen.getByLabelText('Add to Filter by tags');
      for (const tag of ['site:hnoss', 'gpu']) {
        fireEvent.change(input, { target: { value: tag } });
        fireEvent.keyDown(input, { key: 'Enter' });
      }
      expect(screen.getAllByTestId(/^host-row-/).map((r) => r.getAttribute('data-testid'))).toEqual(
        ['host-row-gloi'],
      );
    });

    it('matches any chosen tag when switched to any', async () => {
      setup();
      await row('hrimfaxi');
      const input = screen.getByLabelText('Add to Filter by tags');
      for (const tag of ['gpu', 'arm64']) {
        fireEvent.change(input, { target: { value: tag } });
        fireEvent.keyDown(input, { key: 'Enter' });
      }
      expect(screen.queryAllByTestId(/^host-row-/)).toHaveLength(0);
      fireEvent.click(screen.getByRole('button', { name: 'any' }));
      expect(screen.getAllByTestId(/^host-row-/)).toHaveLength(2);
      fireEvent.click(screen.getByRole('button', { name: 'Remove tag gpu' }));
      expect(screen.getAllByTestId(/^host-row-/)).toHaveLength(1);
    });
  });

  describe('row actions', () => {
    it('checks a host now', async () => {
      const { spies } = setup();
      await row('hrimfaxi');
      fireEvent.click(screen.getByRole('button', { name: 'Check now hrimfaxi' }));
      await waitFor(() =>
        expect(spies.checkComputeHost).toHaveBeenCalledWith('forge', hrimfaxi.id),
      );
    });

    it('checks every shown host with Check all', async () => {
      const { spies } = setup();
      await row('hrimfaxi');
      const input = screen.getByLabelText('Add to Filter by tags');
      fireEvent.change(input, { target: { value: 'gpu' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      fireEvent.click(screen.getByRole('button', { name: 'Check all' }));
      await waitFor(() => expect(spies.checkComputeHost).toHaveBeenCalledTimes(1));
    });

    it('does not offer Check all when no host is shown', async () => {
      setup();
      await row('hrimfaxi');
      const input = screen.getByLabelText('Add to Filter by tags');
      fireEvent.change(input, { target: { value: 'nothing-has-this' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      expect(screen.getByRole('button', { name: 'Check all' })).toBeDisabled();
    });

    it('drains after a confirmation', async () => {
      const { spies } = setup();
      await row('skinfaxi');
      fireEvent.click(screen.getByRole('button', { name: 'Drain skinfaxi' }));
      const dialog = await screen.findByRole('dialog', { name: 'Take skinfaxi out?' });
      expect(spies.drainComputeHost).not.toHaveBeenCalled();
      fireEvent.click(within(dialog).getByRole('button', { name: 'Drain' }));
      await waitFor(() =>
        expect(spies.drainComputeHost).toHaveBeenCalledWith('forge', skinfaxi.id, false),
      );
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    });

    it('drains and removes from its own button', async () => {
      const { spies } = setup();
      await row('skinfaxi');
      fireEvent.click(screen.getByRole('button', { name: 'Drain and remove skinfaxi' }));
      const dialog = await screen.findByRole('dialog');
      fireEvent.click(within(dialog).getByRole('button', { name: 'Drain and remove' }));
      await waitFor(() =>
        expect(spies.drainComputeHost).toHaveBeenCalledWith('forge', skinfaxi.id, true),
      );
    });

    it('closes the confirmation without acting on Cancel', async () => {
      const { spies } = setup();
      await row('skinfaxi');
      fireEvent.click(screen.getByRole('button', { name: 'Drain skinfaxi' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      expect(spies.drainComputeHost).not.toHaveBeenCalled();
    });

    it('removes an empty host and shows the result', async () => {
      const { spies } = setup();
      await row('glad');
      fireEvent.click(screen.getByRole('button', { name: 'Remove glad' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));
      expect(await screen.findByTestId('remove-result')).toHaveTextContent('glad was removed.');
      expect(spies.removeComputeHost).toHaveBeenCalledWith('forge', glad.id);
      fireEvent.click(screen.getByRole('button', { name: 'Done' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      await waitFor(() => expect(screen.queryByTestId('host-row-glad')).toBeNull());
    });

    it('shows the cleanup command for a host Forge could not reach', async () => {
      const lost: ComputeHost = {
        ...glad,
        connection: { ...glad.connection, connected: false },
      };
      setup({ hosts: [lost] });
      await row('glad');
      fireEvent.click(screen.getByRole('button', { name: 'Remove glad' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));
      expect(await screen.findByTestId('remove-result')).toHaveTextContent('sudo niuu-host-leave');
    });

    it('resumes an out-of-service host', async () => {
      const out: ComputeHost = { ...skinfaxi, state: 'out_of_service', sessions: [] };
      const { spies } = setup({ hosts: [out] });
      await row('skinfaxi');
      fireEvent.click(screen.getByRole('button', { name: 'Resume skinfaxi' }));
      await waitFor(() => expect(spies.resumeComputeHost).toHaveBeenCalledWith('forge', out.id));
      expect(await within(await row('skinfaxi')).findByText('Ready')).toBeInTheDocument();
    });

    it('cancels a drain', async () => {
      const draining: ComputeHost = { ...skinfaxi, state: 'draining', service: 'draining' };
      const { spies } = setup({ hosts: [draining] });
      await row('skinfaxi');
      fireEvent.click(screen.getByRole('button', { name: 'Cancel drain skinfaxi' }));
      await waitFor(() =>
        expect(spies.resumeComputeHost).toHaveBeenCalledWith('forge', draining.id),
      );
      expect(await within(await row('skinfaxi')).findByText('In service')).toBeInTheDocument();
    });

    it('re-prepares an empty host', async () => {
      const idle: ComputeHost = { ...hrimfaxi, sessions: [] };
      const { spies } = setup({ hosts: [idle] });
      await row('hrimfaxi');
      fireEvent.click(screen.getByRole('button', { name: 'Re-prepare hrimfaxi' }));
      await waitFor(() =>
        expect(spies.reprepareComputeHost).toHaveBeenCalledWith('forge', idle.id),
      );
      expect(await within(await row('hrimfaxi')).findByText('Ready')).toBeInTheDocument();
    });

    it('shows why an action failed and re-enables the row', async () => {
      const checkComputeHost = vi
        .fn()
        .mockRejectedValue(new ApiClientError('failed', 502, 'probe timed out'));
      setup({ override: { checkComputeHost } });
      await row('hrimfaxi');
      fireEvent.click(screen.getByRole('button', { name: 'Check now hrimfaxi' }));
      expect(await screen.findByText('probe timed out')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Check now hrimfaxi' })).toBeEnabled();
    });

    it('edits tags in place with a PATCH', async () => {
      const { spies } = setup();
      await row('skinfaxi');
      fireEvent.click(screen.getByRole('button', { name: 'Add tag to skinfaxi' }));
      const input = screen.getByLabelText('New tag for skinfaxi');
      fireEvent.change(input, { target: { value: 'gpu' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      await waitFor(() =>
        expect(spies.updateComputeHost).toHaveBeenCalledWith('forge', skinfaxi.id, {
          tags: ['site:hnoss', 'gpu'],
        }),
      );
      expect(await within(await row('skinfaxi')).findByText('gpu')).toBeInTheDocument();
    });
  });

  describe('add host', () => {
    it('opens the Add host flow and returns to the list', async () => {
      setup();
      await row('hrimfaxi');
      fireEvent.click(screen.getByRole('button', { name: 'Add host' }));
      expect(screen.getByRole('heading', { name: 'Add host' })).toBeInTheDocument();
      expect(screen.getByText('Runs as')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Back to hosts' }));
      expect(await row('hrimfaxi')).toBeInTheDocument();
    });

    it('opens a joining host to follow it', async () => {
      setup();
      await row('glad');
      fireEvent.click(screen.getByRole('button', { name: 'Open glad' }));
      expect(await screen.findByText('Connected, key pinned')).toBeInTheDocument();
      expect(screen.queryByText('Runs as')).toBeNull();
    });
  });
});
