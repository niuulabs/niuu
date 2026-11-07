import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ApiClientError } from '@niuulabs/query';
import { createMockComputeHosts, seedComputeHosts } from '../../adapters/computeHosts.mock';
import { createMockVolundrService } from '../../adapters/mock';
import type { ComputeHost, ComputeHostsOverview, NewPairingCode } from '../../domain/computeHosts';
import type { IVolundrService } from '../../ports/IVolundrService';
import { AddHostFlow } from './AddHostFlow';

const NOW = Date.parse('2026-11-06T12:00:00Z');
const glad = seedComputeHosts()[6]!;

async function loadOverview(): Promise<ComputeHostsOverview> {
  const overview = await createMockComputeHosts({ now: () => NOW }).getComputeHosts('forge');
  return { ...overview!, hosts: overview!.hosts.filter((host) => host.name !== 'glad') };
}

function newCode(overrides: Partial<NewPairingCode> = {}): NewPairingCode {
  return {
    id: 'c-1',
    execution: 'host@2026-11-07',
    connector: 'direct-ssh',
    tags: [],
    slots: null,
    expiresAt: new Date(NOW + 9 * 60_000).toISOString(),
    createdBy: 'jozef',
    consumedByHostId: null,
    code: 'jwt-code-0123456789-abcdefghij',
    command: "curl -H 'Authorization: Bearer jwt-code-0123456789-abcdefghij' x | sudo sh",
    commandWget: "wget --header='Authorization: Bearer jwt-code-0123456789-abcdefghij' x | sudo sh",
    script: {
      filename: 'niuu-join.sh',
      sha256: '0'.repeat(64),
      content: '#!/bin/sh\n',
      commands: ['sudo sh niuu-join.sh'],
    },
    ...overrides,
  };
}

interface Rig {
  service: IVolundrService;
  create: ReturnType<typeof vi.fn>;
  revoke: ReturnType<typeof vi.fn>;
  update: ReturnType<typeof vi.fn>;
  check: ReturnType<typeof vi.fn>;
  onClose: ReturnType<typeof vi.fn>;
  show: (overview: ComputeHostsOverview, followHostId?: string) => void;
}

function rig(overview: ComputeHostsOverview, followHostId?: string): Rig {
  const create = vi.fn().mockImplementation(async () => newCode());
  const revoke = vi.fn().mockResolvedValue(undefined);
  const update = vi.fn().mockResolvedValue(glad);
  const check = vi.fn().mockResolvedValue(glad);
  const service: IVolundrService = {
    ...createMockVolundrService(),
    createPairingCode: create,
    revokePairingCode: revoke,
    updateComputeHost: update,
    checkComputeHost: check,
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = vi.fn();
  const tree = (current: ComputeHostsOverview, followHostId?: string): ReactNode => (
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ volundr: service }}>
        <AddHostFlow
          instanceId="forge"
          overview={current}
          onClose={onClose}
          followHostId={followHostId}
        />
      </ServicesProvider>
    </QueryClientProvider>
  );
  const view = render(tree(overview, followHostId));
  return {
    service,
    create,
    revoke,
    update,
    check,
    onClose,
    show: (next, followHostId) => view.rerender(tree(next, followHostId)),
  };
}

beforeEach(() => {
  vi.spyOn(Date, 'now').mockReturnValue(NOW);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('AddHostFlow: the form', () => {
  it('offers each runtime, with the refusal of one that is not allowed', async () => {
    rig(await loadOverview());
    const runtimes = screen.getByRole('radiogroup', { name: 'Runs as' });
    expect(runtimes).toHaveTextContent('Host · Nix + rootless Podman');
    expect(screen.getByRole('radio', { name: /Host · Nix/ })).toBeChecked();
    const docker = screen.getByRole('radio', { name: /Container \(Docker\)/ });
    expect(docker).toBeDisabled();
    expect(runtimes).toHaveTextContent('Needs root Docker, so not on registered hosts.');
    expect(runtimes).toHaveTextContent('Exclusive hosts only.');
  });

  it('says a refused runtime without a reason is not available', async () => {
    const overview = await loadOverview();
    rig({
      ...overview,
      runtimes: [{ ...overview.runtimes[2]!, refusal: null }, overview.runtimes[0]!],
    });
    expect(screen.getByText('Not available on registered hosts.')).toBeInTheDocument();
  });

  it('offers the connectors the Forge has configured with a line each', async () => {
    rig(await loadOverview());
    const connectors = screen.getByRole('radiogroup', { name: 'How Forge reaches it' });
    expect(connectors).toHaveTextContent('Forge connects over SSH');
    expect(connectors).toHaveTextContent('Forge can reach the host on your network.');
    expect(connectors).toHaveTextContent('Host connects out');
    expect(connectors).toHaveTextContent('Behind NAT or a firewall');
  });

  it('creates the command with the default choices', async () => {
    const flow = rig(await loadOverview());
    fireEvent.click(screen.getByRole('button', { name: 'Create command' }));
    expect(await screen.findByLabelText('Join command')).toBeInTheDocument();
    expect(flow.create).toHaveBeenCalledWith('forge', {
      execution: 'host@2026-11-07',
      connector: 'direct-ssh',
      tags: [],
      slots: null,
    });
    expect(screen.getByText('Run this on the host')).toBeInTheDocument();
    expect(screen.getByText('Waiting for a host…')).toBeInTheDocument();
  });

  it('sends the tags, the connector and the slot count that were chosen', async () => {
    const flow = rig(await loadOverview());
    const input = screen.getByLabelText('Add to Host tags');
    fireEvent.change(input, { target: { value: 'site:hnoss' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.click(screen.getByRole('radio', { name: /Host connects out/ }));
    fireEvent.click(screen.getByRole('radio', { name: 'Now' }));
    fireEvent.change(screen.getByLabelText('Slots'), { target: { value: '6' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create command' }));
    await screen.findByLabelText('Join command');
    expect(flow.create).toHaveBeenCalledWith('forge', {
      execution: 'host@2026-11-07',
      connector: 'reverse-ssh',
      tags: ['site:hnoss'],
      slots: 6,
    });
  });

  it('holds Create command until a chosen slot count is a positive whole number', async () => {
    rig(await loadOverview());
    const create = screen.getByRole('button', { name: 'Create command' });
    expect(screen.getByLabelText('Slots')).toBeDisabled();
    fireEvent.click(screen.getByRole('radio', { name: 'Now' }));
    expect(create).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Slots'), { target: { value: '0' } });
    expect(create).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Slots'), { target: { value: '3' } });
    expect(create).toBeEnabled();
    fireEvent.click(screen.getByRole('radio', { name: 'After the first check' }));
    expect(create).toBeEnabled();
  });

  it('fixes one slot for a runtime that runs one session per host', async () => {
    const flow = rig(await loadOverview());
    fireEvent.click(screen.getByRole('radio', { name: /OpenShell/ }));
    expect(screen.getByLabelText('Slots')).toHaveValue('1');
    expect(screen.getByLabelText('Slots')).toBeDisabled();
    expect(screen.getByText('This runtime runs one session per host.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create command' }));
    await screen.findByLabelText('Join command');
    expect(flow.create).toHaveBeenCalledWith('forge', {
      execution: 'openshell@2026-11-07',
      connector: 'direct-ssh',
      tags: [],
      slots: 1,
    });
  });

  it('cannot create a command when no runtime is allowed', async () => {
    const overview = await loadOverview();
    rig({ ...overview, runtimes: [overview.runtimes[2]!] });
    expect(screen.getByRole('button', { name: 'Create command' })).toBeDisabled();
  });

  it('cannot create a command when the Forge has no connector', async () => {
    const overview = await loadOverview();
    rig({ ...overview, connectors: [] });
    expect(screen.getByRole('button', { name: 'Create command' })).toBeDisabled();
  });

  it('shows why the Forge refused to mint a command', async () => {
    const flow = rig(await loadOverview());
    flow.create.mockRejectedValue(new ApiClientError('failed', 422, 'unknown connector'));
    fireEvent.click(screen.getByRole('button', { name: 'Create command' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('unknown connector');
    expect(screen.queryByLabelText('Join command')).toBeNull();
    expect(screen.getByRole('button', { name: 'Create command' })).toBeEnabled();
  });

  it('locks the form while the command waits, and revoking unlocks it', async () => {
    const flow = rig(await loadOverview());
    fireEvent.click(screen.getByRole('button', { name: 'Create command' }));
    await screen.findByLabelText('Join command');
    expect(screen.getByRole('radio', { name: /Host connects out/ })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Create command' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(screen.queryByLabelText('Join command')).toBeNull());
    expect(flow.revoke).toHaveBeenCalledWith('forge', 'c-1');
    expect(screen.getByRole('button', { name: 'Create command' })).toBeEnabled();
  });

  it('shows why a revoke failed and keeps the command', async () => {
    const flow = rig(await loadOverview());
    flow.revoke.mockRejectedValue(new ApiClientError('failed', 404, 'code already used'));
    fireEvent.click(screen.getByRole('button', { name: 'Create command' }));
    await screen.findByLabelText('Join command');
    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('code already used');
    expect(screen.getByLabelText('Join command')).toBeInTheDocument();
  });

  it('offers a new command once the first one expired', async () => {
    const flow = rig(await loadOverview());
    flow.create.mockResolvedValue(newCode({ expiresAt: new Date(NOW - 1_000).toISOString() }));
    fireEvent.click(screen.getByRole('button', { name: 'Create command' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Create a new command' }));
    expect(screen.getByRole('button', { name: 'Create command' })).toBeInTheDocument();
  });
});

/** Creates the command for tags that match the seeded `glad` host. */
async function createForGlad() {
  const input = screen.getByLabelText('Add to Host tags');
  for (const tag of ['site:hnoss', 'fast-disk']) {
    fireEvent.change(input, { target: { value: tag } });
    fireEvent.keyDown(input, { key: 'Enter' });
  }
  fireEvent.click(screen.getByRole('button', { name: 'Create command' }));
  await screen.findByLabelText('Join command');
}

describe('AddHostFlow: following the host', () => {
  it('follows the host that ran the command and shows where Forge is with it', async () => {
    const overview = await loadOverview();
    const flow = rig(overview);
    await createForGlad();
    act(() => flow.show({ ...overview, hosts: [...overview.hosts, glad] }));
    expect(await screen.findByText('Connected, key pinned')).toBeInTheDocument();
    expect(screen.getByText('glad')).toBeInTheDocument();
    expect(screen.queryByLabelText('Join command')).toBeNull();
  });

  it('is not fooled by a host that was already there', async () => {
    const overview = await loadOverview();
    const flow = rig({ ...overview, hosts: [...overview.hosts, glad] });
    fireEvent.click(screen.getByRole('button', { name: 'Create command' }));
    await screen.findByLabelText('Join command');
    act(() => flow.show({ ...overview, hosts: [...overview.hosts, glad] }));
    expect(screen.getByLabelText('Join command')).toBeInTheDocument();
  });

  it('asks for the capacity once the host is healthy, and sets it', async () => {
    const overview = await loadOverview();
    const flow = rig(overview);
    await createForGlad();
    const healthy: ComputeHost = {
      ...glad,
      state: 'ready',
      health: 'healthy',
      suggestedSlots: { slots: 11, lines: ['CPU (48 − 4) ÷ 4 = 11'] },
    };
    act(() => flow.show({ ...overview, hosts: [...overview.hosts, healthy] }));
    expect(await screen.findByText('Prepared and audited')).toBeInTheDocument();
    expect(screen.getByText('Each session on this host gets')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Put in service' }));
    await waitFor(() => expect(flow.update).toHaveBeenCalledWith('forge', glad.id, { slots: 11 }));
    act(() => flow.show({ ...overview, hosts: [...overview.hosts, { ...healthy, slots: 11 }] }));
    expect(await screen.findByText('Set capacity')).toBeInTheDocument();
    expect(screen.queryByText('Prepared and audited')).toBeNull();
  });

  it('goes back to the list when the capacity is left for later', async () => {
    const overview = await loadOverview();
    const flow = rig(overview);
    await createForGlad();
    act(() =>
      flow.show({
        ...overview,
        hosts: [...overview.hosts, { ...glad, state: 'ready', health: 'healthy' }],
      }),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Keep out of service' }));
    expect(flow.onClose).toHaveBeenCalled();
  });

  it('opens a host that is still joining when opened on it', async () => {
    const overview = await loadOverview();
    rig({ ...overview, hosts: [...overview.hosts, glad] }, glad.id);
    expect(screen.getByText('Connected, key pinned')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create command' })).toBeNull();
  });

  it('can check a host that failed its check', async () => {
    const overview = await loadOverview();
    const quarantined: ComputeHost = { ...glad, state: 'quarantined', health: 'quarantined' };
    const flow = rig({ ...overview, hosts: [...overview.hosts, quarantined] }, glad.id);
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
    await waitFor(() => expect(flow.check).toHaveBeenCalledWith('forge', glad.id));
  });

  it('shows the form when the followed host is gone', async () => {
    const overview = await loadOverview();
    rig(overview, 'removed-host');
    expect(screen.getByRole('button', { name: 'Create command' })).toBeInTheDocument();
  });
});
