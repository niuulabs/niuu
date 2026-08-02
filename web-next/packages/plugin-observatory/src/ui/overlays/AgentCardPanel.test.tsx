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

    // The kind moved to the inspector's own eyebrow; the card states what
    // the card states.
    expect(screen.getByText('Agent card')).toBeInTheDocument();
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

    expect(screen.getByText('Skills it advertises')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('gpu_pressure_probe')).toBeInTheDocument();
    // Both forms: the identifier a caller sends, and a name a reader scans.
    expect(screen.getByText('Gpu pressure probe')).toBeInTheDocument();
  });

  it('names a skill the way its card does, not by its id', async () => {
    // Ting's skill ids are UUIDs. Deriving a label from the id printed
    // `03dba0d8 4f29 560a a4e1 1fc7e5f9ee23` — the id again, with spaces.
    renderPanel(
      directoryReturning([
        entry({
          skillIds: ['03dba0d8-4f29-560a-a4e1-1fc7e5f9ee23'],
          skills: [{ id: '03dba0d8-4f29-560a-a4e1-1fc7e5f9ee23', name: 'Refactor a service' }],
        }),
      ]),
    );

    await waitFor(() => expect(screen.getByTestId('agent-card-skills')).toBeInTheDocument());
    expect(screen.getByText('Refactor a service')).toBeInTheDocument();
    // The opaque id is not shown alongside it — it tells the reader nothing.
    expect(screen.queryByText(/03dba0d8/)).not.toBeInTheDocument();
  });

  it('still derives a label when a card publishes no skill names', async () => {
    renderPanel(directoryReturning([entry({ skillIds: ['gpu_pressure_probe'], skills: [] })]));

    await waitFor(() => expect(screen.getByTestId('agent-card-skills')).toBeInTheDocument());
    expect(screen.getByText('Gpu pressure probe')).toBeInTheDocument();
    // A meaningful id is still worth showing.
    expect(screen.getByText('gpu_pressure_probe')).toBeInTheDocument();
  });

  it('keeps the skills section when the card advertises none', async () => {
    // The section says the agent offers nothing, which is a reading. A missing
    // section says only that this panel forgot to mention skills.
    renderPanel(directoryReturning([entry({ skillIds: [] })]));
    await waitFor(() => expect(screen.getByTestId('agent-card')).toBeInTheDocument());

    expect(screen.getByText('Skills it advertises')).toBeInTheDocument();
    expect(screen.getByText('None advertised.')).toBeInTheDocument();
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
