import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { EntityDrawer } from './EntityDrawer';
import { createMockTopologyStream, createMockRegistryRepository } from '../../adapters/mock';
import type { TopologyNode, Topology, Registry } from '../../domain';

// ---------------------------------------------------------------------------
// Synchronous test fixtures pulled from mock adapters
// ---------------------------------------------------------------------------

let REGISTRY: Registry;
let TOPOLOGY: Topology;

beforeAll(async () => {
  REGISTRY = await createMockRegistryRepository().getRegistry();
  TOPOLOGY = createMockTopologyStream().getSnapshot()!;
});

const REALM_NODE: TopologyNode = {
  id: 'realm-asgard',
  typeId: 'realm',
  label: 'asgard',
  parentId: null,
  status: 'healthy',
  vlan: 90,
  dns: 'asgard.niuu.world',
  purpose: 'AI / compute / dev',
  zone: 'asgard',
};

const CLUSTER_NODE: TopologyNode = {
  id: 'cluster-valaskjalf',
  typeId: 'cluster',
  label: 'valaskjálf',
  parentId: 'realm-asgard',
  status: 'healthy',
  zone: 'asgard',
  purpose: 'DGX Spark cluster',
};

const HOST_NODE: TopologyNode = {
  id: 'host-mjolnir',
  typeId: 'host',
  label: 'mjölnir',
  parentId: 'realm-asgard',
  status: 'healthy',
  zone: 'asgard',
  hw: 'DGX Spark',
  os: 'Ubuntu 24',
  cores: 144,
};

const TYR_NODE: TopologyNode = {
  id: 'tyr-0',
  typeId: 'tyr',
  label: 'tyr-0',
  parentId: 'cluster-valaskjalf',
  status: 'healthy',
  zone: 'asgard',
  mode: 'active',
  activeSagas: 3,
  pendingRaids: 2,
  activity: 'thinking',
};

const DEGRADED_NODE: TopologyNode = {
  id: 'svc-1',
  typeId: 'service',
  label: 'my-svc',
  parentId: 'cluster-valaskjalf',
  status: 'degraded',
  svcType: 'database',
};

const RAVN_NODE: TopologyNode = {
  id: 'ravn-huginn',
  typeId: 'ravn_long',
  label: 'huginn',
  parentId: 'host-mjolnir',
  status: 'healthy',
  zone: 'asgard',
  hostId: 'host-mjolnir',
  persona: 'thought',
  specialty: 'architecture & design',
  tokens: 42800,
  activity: 'thinking',
};

const BIFROST_NODE: TopologyNode = {
  id: 'bifrost-0',
  typeId: 'bifrost',
  label: 'bifröst-0',
  parentId: 'cluster-valaskjalf',
  status: 'healthy',
  zone: 'asgard',
  providers: ['Anthropic', 'OpenAI'],
  reqPerMin: 42,
  cacheHitRate: 0.68,
  activity: 'idle',
};

const VOLUNDR_NODE: TopologyNode = {
  id: 'volundr-0',
  typeId: 'volundr',
  label: 'völundr-0',
  parentId: 'cluster-valhalla',
  status: 'healthy',
  activeSessions: 5,
  maxSessions: 20,
};

function renderDrawer(
  node: TopologyNode | null,
  overrides?: {
    topology?: Topology | null;
    registry?: Registry | null;
    onNodeSelect?: (n: TopologyNode) => void;
  },
) {
  const onClose = vi.fn();
  return {
    onClose,
    ...render(
      <EntityDrawer
        node={node}
        topology={overrides?.topology !== undefined ? overrides.topology : TOPOLOGY}
        registry={overrides?.registry !== undefined ? overrides.registry : REGISTRY}
        onClose={onClose}
        onNodeSelect={overrides?.onNodeSelect}
      />,
    ),
  };
}

describe('EntityDrawer', () => {
  it('renders nothing visible when node is null (drawer closed)', () => {
    renderDrawer(null);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens with the node label as the drawer title for realm', () => {
    renderDrawer(REALM_NODE);
    expect(screen.getByRole('dialog', { name: /asgard/i })).toBeInTheDocument();
  });

  it('opens with the node label as the drawer title for cluster', () => {
    renderDrawer(CLUSTER_NODE);
    expect(screen.getByRole('dialog', { name: /valaskjálf/i })).toBeInTheDocument();
  });

  it('shows realm eyebrow text', () => {
    renderDrawer(REALM_NODE);
    expect(screen.getByText(/Realm · VLAN zone/i)).toBeInTheDocument();
  });

  it('shows cluster eyebrow text', () => {
    renderDrawer(CLUSTER_NODE);
    expect(screen.getByText(/Cluster · k8s/i)).toBeInTheDocument();
  });

  it('shows entity type label and rune in head for tyr', () => {
    renderDrawer(TYR_NODE);
    expect(screen.getByText('ᛃ')).toBeInTheDocument();
    expect(screen.getByText(/Týr/)).toBeInTheDocument();
  });

  it('shows node status text for tyr', () => {
    renderDrawer(TYR_NODE);
    expect(screen.getByText('healthy')).toBeInTheDocument();
  });

  it('shows degraded status for a degraded node', () => {
    renderDrawer(DEGRADED_NODE);
    expect(screen.getByText('degraded')).toBeInTheDocument();
  });

  it('shows realm vlan chip when vlan is set', () => {
    renderDrawer(REALM_NODE);
    expect(screen.getByText('vlan 90')).toBeInTheDocument();
  });

  it('shows realm About section with dns', () => {
    renderDrawer(REALM_NODE);
    expect(screen.getByText('asgard.niuu.world')).toBeInTheDocument();
  });

  it('shows realm residents section with children from topology', () => {
    renderDrawer(REALM_NODE);
    // TOPOLOGY has cluster-valaskjalf and cluster-valhalla and host-mjolnir with parentId realm-asgard
    expect(screen.getByText('Residents')).toBeInTheDocument();
    expect(screen.getByText('valaskjálf')).toBeInTheDocument();
  });

  it('shows cluster members section', () => {
    renderDrawer(CLUSTER_NODE);
    expect(screen.getByText('Members')).toBeInTheDocument();
    expect(screen.getByText('tyr-0')).toBeInTheDocument();
  });

  it('shows host residents section', () => {
    renderDrawer(HOST_NODE);
    expect(screen.getByText('Residents')).toBeInTheDocument();
    expect(screen.getByText('huginn')).toBeInTheDocument();
  });

  it('shows tyr Properties section', () => {
    renderDrawer(TYR_NODE);
    expect(screen.getByText('Properties')).toBeInTheDocument();
    expect(screen.getByText('active sagas')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('shows tyr mode badge', () => {
    renderDrawer(TYR_NODE);
    expect(screen.getByText('active')).toBeInTheDocument();
  });

  it('shows bifrost Properties with cache hit rate', () => {
    renderDrawer(BIFROST_NODE);
    expect(screen.getByText('Properties')).toBeInTheDocument();
    expect(screen.getByText('68%')).toBeInTheDocument();
    expect(screen.getByText('Anthropic, OpenAI')).toBeInTheDocument();
  });

  it('shows volundr Properties with sessions', () => {
    renderDrawer(VOLUNDR_NODE);
    expect(screen.getByText('Properties')).toBeInTheDocument();
    expect(screen.getByText('5 / 20')).toBeInTheDocument();
  });

  it('shows ravn_long Properties with persona and tokens', () => {
    renderDrawer(RAVN_NODE);
    expect(screen.getByText('Properties')).toBeInTheDocument();
    expect(screen.getByText('thought')).toBeInTheDocument();
    expect(screen.getByText('42,800')).toBeInTheDocument();
  });

  it('shows Identity section for entity nodes', () => {
    renderDrawer(TYR_NODE);
    expect(screen.getByText('Identity')).toBeInTheDocument();
    // tyr-0 appears as both the drawer title and the identity id — getAllByText handles both
    expect(screen.getAllByText('tyr-0').length).toBeGreaterThanOrEqual(1);
  });

  it('shows Actions section for entity nodes', () => {
    renderDrawer(TYR_NODE);
    expect(screen.getByText('Actions')).toBeInTheDocument();
    expect(screen.getByText('Open chat')).toBeInTheDocument();
  });

  it('shows activity row when activity is set', () => {
    renderDrawer(TYR_NODE);
    expect(screen.getByText('THINKING')).toBeInTheDocument();
  });

  it('calls onNodeSelect when a resident button is clicked (realm)', () => {
    const onNodeSelect = vi.fn();
    renderDrawer(REALM_NODE, { onNodeSelect });
    const clusterBtn = screen.getByTestId('resident-cluster-valaskjalf');
    fireEvent.click(clusterBtn);
    expect(onNodeSelect).toHaveBeenCalledOnce();
  });

  it('calls onNodeSelect when a member button is clicked (cluster)', () => {
    const onNodeSelect = vi.fn();
    renderDrawer(CLUSTER_NODE, { onNodeSelect });
    const tyrBtn = screen.getByTestId('resident-tyr-0');
    fireEvent.click(tyrBtn);
    expect(onNodeSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'tyr-0' }));
  });

  it('does not show Residents when realm has no children', () => {
    const emptyTopology: Topology = { nodes: [REALM_NODE], edges: [], timestamp: '' };
    renderDrawer(REALM_NODE, { topology: emptyTopology });
    expect(screen.queryByText('Residents')).toBeNull();
  });

  it('calls onClose when drawer close button is clicked', () => {
    const { onClose } = renderDrawer(TYR_NODE);
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('handles null topology gracefully (no residents)', () => {
    renderDrawer(REALM_NODE, { topology: null });
    expect(screen.queryByText('Residents')).toBeNull();
  });

  it('handles null registry gracefully (no rune, fallback label)', () => {
    renderDrawer(TYR_NODE, { registry: null });
    expect(screen.getByText('tyr')).toBeInTheDocument();
  });

  it('shows all NodeStatus variants without error', () => {
    const statuses = ['healthy', 'degraded', 'failed', 'idle', 'observing', 'unknown'] as const;
    for (const status of statuses) {
      const { unmount } = render(
        <EntityDrawer
          node={{ id: 'x', typeId: 'service', label: 'x', parentId: null, status }}
          topology={TOPOLOGY}
          registry={REGISTRY}
          onClose={vi.fn()}
        />,
      );
      expect(screen.getByText(status)).toBeInTheDocument();
      unmount();
    }
  });

  it('shows typeId as fallback label when entity type not in registry', () => {
    renderDrawer({
      id: 'x',
      typeId: 'unknown-type',
      label: 'x',
      parentId: null,
      status: 'unknown',
    });
    expect(screen.getByText('unknown-type')).toBeInTheDocument();
  });

  it('calls onClose when Escape key is pressed', () => {
    const { onClose } = renderDrawer(TYR_NODE);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('does not add Escape listener when drawer is closed (node is null)', () => {
    const { onClose } = renderDrawer(null);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });
});
