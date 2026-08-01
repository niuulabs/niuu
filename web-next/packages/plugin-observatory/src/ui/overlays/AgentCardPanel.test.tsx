import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { AgentCardPanel } from './AgentCardPanel';
import type { AgentDirectoryEntry, AgentDirectoryPage, TopologyNode } from '../../domain';
import type { IAgentDirectory } from '../../ports';

const NODE: TopologyNode = {
  id: 'ravn-huginn',
  typeId: 'ravn_long',
  label: 'huginn',
  parentId: 'cluster-valaskjalf',
  status: 'healthy',
};

function entry(overrides: Partial<AgentDirectoryEntry> = {}): AgentDirectoryEntry {
  return {
    id: 'agent-ravn-huginn',
    canonicalId: 'niuu:agent:ravn-huginn',
    sourceAgentId: 'ravn-huginn',
    sourceInstanceId: 'observatory-asgard',
    clusterId: 'asgard',
    environmentId: null,
    topologyNodeId: 'ravn-huginn',
    name: 'huginn',
    description: 'Cluster resident for the GPU estate.',
    kind: 'resident',
    cardUrl: 'https://huginn.asgard.niuu.world/.well-known/agent-card.json',
    cardVersion: '0.0.1',
    cardHash: 'sha256:abc',
    signatureVerified: true,
    signatureKeyIds: ['niuu-a2a-signing'],
    signatureKeyFingerprints: ['SHA256:fp'],
    skillIds: ['gpu_pressure_probe', 'replica_warm'],
    tags: ['resident'],
    defaultInputModes: ['text/plain'],
    defaultOutputModes: ['text/plain'],
    supportedInterfaces: [
      {
        url: 'https://huginn.asgard.niuu.world/a2a',
        protocolBinding: 'JSONRPC',
        protocolVersion: '0.3.0',
        tenant: 'niuu.world',
      },
    ],
    capabilities: { streaming: true, pushNotifications: false, stateTransitionHistory: true },
    securitySchemes: {},
    securityRequirements: [],
    observedStatus: 'healthy',
    activity: 'thinking',
    lastSeen: '2026-08-01T12:00:00Z',
    ownerId: null,
    tenantId: 'niuu.world',
    visibility: 'realm',
    provenance: [],
    ...overrides,
  };
}

function page(items: AgentDirectoryEntry[]): AgentDirectoryPage {
  return { items, warnings: [], sources: [], partial: false, revision: 'r1' };
}

function renderPanel(directory: IAgentDirectory, node: TopologyNode | null = NODE) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ 'observatory.agents': directory }}>
          {children}
        </ServicesProvider>
      </QueryClientProvider>
    );
  }
  return render(<AgentCardPanel node={node} />, { wrapper: Wrapper });
}

function directoryReturning(items: AgentDirectoryEntry[]): IAgentDirectory {
  return { listAgents: vi.fn(async () => page(items)), getAgent: vi.fn() };
}

describe('AgentCardPanel', () => {
  it('renders nothing when no node is selected', () => {
    const { container } = renderPanel(directoryReturning([entry()]), null);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows a loading note while the directory resolves', () => {
    const directory = {
      listAgents: vi.fn(() => new Promise<AgentDirectoryPage>(() => {})),
      getAgent: vi.fn(),
    } as IAgentDirectory;
    renderPanel(directory);
    expect(screen.getByTestId('agent-card-loading')).toBeInTheDocument();
  });

  it('reports an unavailable directory rather than failing silently', async () => {
    const directory = {
      listAgents: vi.fn(async () => {
        throw new Error('directory offline');
      }),
      getAgent: vi.fn(),
    } as IAgentDirectory;
    renderPanel(directory);
    await waitFor(() => expect(screen.getByTestId('agent-card-error')).toBeInTheDocument());
    expect(screen.getByTestId('agent-card-error')).toHaveTextContent('directory offline');
  });

  it('renders nothing for a node that publishes no card', async () => {
    const { container } = renderPanel(
      directoryReturning([entry({ topologyNodeId: 'somewhere-else' })]),
    );
    // Wait for the fetch to settle, then assert the panel left no empty shell.
    await waitFor(() => expect(screen.queryByTestId('agent-card-loading')).toBeNull());
    expect(container).toBeEmptyDOMElement();
  });

  it('renders the card for the selected node', async () => {
    renderPanel(directoryReturning([entry()]));
    await waitFor(() => expect(screen.getByTestId('agent-card')).toBeInTheDocument());

    expect(screen.getByTestId('agent-card-kind')).toHaveTextContent('resident');
    expect(screen.getByTestId('agent-card-url')).toHaveTextContent('.well-known/agent-card.json');
    expect(screen.getByText('JSONRPC')).toBeInTheDocument();
    expect(screen.getByText('A2A 0.3.0')).toBeInTheDocument();
    expect(screen.getByText('realm')).toBeInTheDocument();
  });

  it('marks each declared capability as on or off', async () => {
    renderPanel(directoryReturning([entry()]));
    await waitFor(() => expect(screen.getByTestId('agent-card')).toBeInTheDocument());

    expect(screen.getByTestId('agent-cap-streaming')).toHaveAttribute('data-enabled', 'true');
    expect(screen.getByTestId('agent-cap-pushNotifications')).toHaveAttribute(
      'data-enabled',
      'false',
    );
    expect(screen.getByTestId('agent-cap-stateTransitionHistory')).toHaveAttribute(
      'data-enabled',
      'true',
    );
  });

  it('distinguishes verified, invalid and unsigned cards', async () => {
    const { unmount } = renderPanel(directoryReturning([entry()]));
    await waitFor(() =>
      expect(screen.getByTestId('agent-card-signature')).toHaveTextContent('signature verified'),
    );
    unmount();

    const invalid = renderPanel(directoryReturning([entry({ signatureVerified: false })]));
    await waitFor(() =>
      expect(screen.getByTestId('agent-card-signature')).toHaveTextContent('signature invalid'),
    );
    invalid.unmount();

    renderPanel(directoryReturning([entry({ signatureVerified: null })]));
    await waitFor(() =>
      expect(screen.getByTestId('agent-card-signature')).toHaveTextContent('unsigned'),
    );
  });

  it('lists the advertised skills with a count', async () => {
    renderPanel(directoryReturning([entry()]));
    await waitFor(() => expect(screen.getByTestId('agent-card-skills')).toBeInTheDocument());

    expect(screen.getByText('Skills · 2')).toBeInTheDocument();
    expect(screen.getByText('gpu_pressure_probe')).toBeInTheDocument();
  });

  it('omits the skills block when the card advertises none', async () => {
    renderPanel(directoryReturning([entry({ skillIds: [] })]));
    await waitFor(() => expect(screen.getByTestId('agent-card')).toBeInTheDocument());
    expect(screen.queryByTestId('agent-card-skills')).toBeNull();
  });

  it('tolerates a card that declares no interface', async () => {
    renderPanel(directoryReturning([entry({ supportedInterfaces: [] })]));
    await waitFor(() => expect(screen.getByTestId('agent-card')).toBeInTheDocument());
    expect(screen.queryByText('JSONRPC')).toBeNull();
  });

  it('omits the description when the card carries none', async () => {
    renderPanel(directoryReturning([entry({ description: '' })]));
    await waitFor(() => expect(screen.getByTestId('agent-card')).toBeInTheDocument());
    expect(screen.queryByText('Cluster resident for the GPU estate.')).toBeNull();
  });
});
