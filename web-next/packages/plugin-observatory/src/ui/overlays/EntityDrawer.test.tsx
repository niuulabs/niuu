import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { EntityDrawer } from './EntityDrawer';
import { createMockRegistryRepository } from '../../adapters/mock';
import type { TopologyNode, Topology, Registry } from '../../domain';

// ---------------------------------------------------------------------------
// Synchronous test fixtures pulled from mock adapters
// ---------------------------------------------------------------------------

let REGISTRY: Registry;
let TOPOLOGY: Topology;

beforeAll(async () => {
  REGISTRY = await createMockRegistryRepository().getRegistry();
  TOPOLOGY = fixtureTopology();
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

const TING_NODE: TopologyNode = {
  id: 'ting-0',
  typeId: 'ting',
  label: 'ting-0',
  parentId: 'cluster-valaskjalf',
  status: 'healthy',
  zone: 'asgard',
  mode: 'active',
  activeSagas: 3,
  pendingRuns: 2,
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

const RUN_NODE: TopologyNode = {
  id: 'run-refactor',
  typeId: 'run',
  label: 'run-refactor',
  parentId: 'cluster-valaskjalf',
  status: 'observing',
  zone: 'asgard',
  state: 'working',
  purpose: 'refactor bifrost routing',
  flockId: 'workflow-refactor',
};

const MIMIR_NODE: TopologyNode = {
  id: 'mimir-archive',
  typeId: 'mimir',
  label: 'mimir-archive',
  parentId: 'host-mjolnir',
  status: 'healthy',
  pages: 12,
  writes: 4,
  mountCount: 2,
  mounts: ['/memory', '/archive'],
};

const VALKYRIE_NODE: TopologyNode = {
  id: 'valkyrie-ops',
  typeId: 'valkyrie',
  label: 'valkyrie-ops',
  parentId: 'cluster-valaskjalf',
  status: 'healthy',
  specialty: 'incident response',
  autonomy: 'high',
};

const PRINTER_NODE: TopologyNode = {
  id: 'printer-brokk',
  typeId: 'printer',
  label: 'brokk',
  parentId: 'realm-asgard',
  status: 'healthy',
  model: 'Ultimaker S7',
};

const VAETTIR_NODE: TopologyNode = {
  id: 'vaettir-watch',
  typeId: 'vaettir',
  label: 'watch',
  parentId: 'realm-asgard',
  status: 'healthy',
  sensors: 'temp, humidity',
};

const MODEL_NODE: TopologyNode = {
  id: 'model-sonnet',
  typeId: 'model',
  label: 'claude-sonnet',
  parentId: 'bifrost-0',
  status: 'healthy',
  provider: 'Anthropic',
  location: 'us-east',
};

/**
 * The drawer resolves parents, members and residents out of the topology it is
 * handed, so these tests own a small fixture rather than reaching for the demo
 * seed. Otherwise a seed edit breaks tests of a component it has nothing to do
 * with — which is exactly what happened when the seed grew.
 */
function fixtureTopology(): Topology {
  return {
    timestamp: '2026-08-01T12:00:00Z',
    nodes: [
      REALM_NODE,
      CLUSTER_NODE,
      HOST_NODE,
      TING_NODE,
      RAVN_NODE,
      BIFROST_NODE,
      VOLUNDR_NODE,
      RUN_NODE,
      MIMIR_NODE,
      VALKYRIE_NODE,
      PRINTER_NODE,
      VAETTIR_NODE,
      MODEL_NODE,
      DEGRADED_NODE,
    ],
    edges: [],
  };
}

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

  it('shows entity type label and rune in head for ting', () => {
    renderDrawer(TING_NODE);
    expect(screen.getByText('✦')).toBeInTheDocument();
    expect(screen.getByText(/Ting/)).toBeInTheDocument();
  });

  it('shows node status text for ting', () => {
    renderDrawer(TING_NODE);
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
    expect(screen.getByText('ting-0')).toBeInTheDocument();
  });

  it('shows host residents section', () => {
    renderDrawer(HOST_NODE);
    expect(screen.getByText('Residents')).toBeInTheDocument();
    expect(screen.getByText('huginn')).toBeInTheDocument();
  });

  it('shows run members section with ordered workflow nodes', () => {
    const runTopology: Topology = {
      nodes: [
        RUN_NODE,
        {
          id: 'run-refactor-trigger',
          typeId: 'trigger',
          label: 'code requested',
          parentId: 'run-refactor',
          status: 'healthy',
          layoutHints: { order: 0, packGroup: 'entry' },
        },
        {
          id: 'run-refactor-analyze',
          typeId: 'stage',
          label: 'analyze',
          parentId: 'run-refactor',
          status: 'healthy',
          layoutHints: { order: 2, packGroup: 'main' },
        },
        {
          id: 'run-refactor-review',
          typeId: 'gate',
          label: 'review',
          parentId: 'run-refactor',
          status: 'healthy',
          layoutHints: { order: 3, packGroup: 'decision' },
        },
      ],
      edges: [],
      timestamp: '2026-05-24T16:00:00Z',
    };

    renderDrawer(RUN_NODE, { topology: runTopology });
    expect(screen.getByText('Members')).toBeInTheDocument();
    expect(screen.getByText('working')).toBeInTheDocument();
    expect(screen.getByText('code requested')).toBeInTheDocument();
    expect(screen.getByText('analyze')).toBeInTheDocument();
    expect(screen.getByText('review')).toBeInTheDocument();
  });

  it('shows ting Properties section', () => {
    renderDrawer(TING_NODE);
    expect(screen.getByText('Properties')).toBeInTheDocument();
    expect(screen.getByText('active sagas')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('shows ting mode badge', () => {
    renderDrawer(TING_NODE);
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

  it('shows bifrost fallback values when optional metrics are missing', () => {
    renderDrawer({
      ...BIFROST_NODE,
      id: 'bifrost-fallback',
      providers: undefined,
      reqPerMin: undefined,
      cacheHitRate: undefined,
    });
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
    expect(screen.getByText('0')).toBeInTheDocument();
  });

  it('shows ravn_long Properties with persona and tokens', () => {
    renderDrawer(RAVN_NODE);
    expect(screen.getByText('Properties')).toBeInTheDocument();
    expect(screen.getByText('thought')).toBeInTheDocument();
    expect(screen.getByText('42,800')).toBeInTheDocument();
  });

  it('shows mimir Properties including mounts and surface', () => {
    renderDrawer(MIMIR_NODE);
    expect(screen.getByText('pages')).toBeInTheDocument();
    expect(screen.getByText('/memory, /archive')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('shows valkyrie, printer, vaettir, and model properties', () => {
    const nodes = [VALKYRIE_NODE, PRINTER_NODE, VAETTIR_NODE, MODEL_NODE];

    for (const node of nodes) {
      const { unmount } = render(
        <EntityDrawer node={node} topology={TOPOLOGY} registry={REGISTRY} onClose={vi.fn()} />,
      );

      expect(screen.getByText('Properties')).toBeInTheDocument();
      unmount();
    }

    renderDrawer(VALKYRIE_NODE);
    expect(screen.getByText('incident response')).toBeInTheDocument();
    expect(screen.getByText('high')).toBeInTheDocument();
  });

  it('shows Identity section for entity nodes', () => {
    renderDrawer(TING_NODE);
    expect(screen.getByText('Identity')).toBeInTheDocument();
    // ting-0 appears as both the drawer title and the identity id — getAllByText handles both
    expect(screen.getAllByText('ting-0').length).toBeGreaterThanOrEqual(1);
  });

  it('shows Actions section for entity nodes', () => {
    renderDrawer(TING_NODE);
    expect(screen.getByText('Actions')).toBeInTheDocument();
    expect(screen.getByText('Open chat')).toBeInTheDocument();
  });

  it('shows activity row when activity is set', () => {
    renderDrawer(TING_NODE);
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
    const tingBtn = screen.getByTestId('resident-ting-0');
    fireEvent.click(tingBtn);
    expect(onNodeSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'ting-0' }));
  });

  it('calls onNodeSelect when the host link is clicked', () => {
    const onNodeSelect = vi.fn();
    renderDrawer(RAVN_NODE, { onNodeSelect });
    fireEvent.click(screen.getByRole('button', { name: 'host-mjolnir' }));
    expect(onNodeSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'host-mjolnir' }));
  });

  it('does not call onNodeSelect when the host link target is missing', () => {
    const onNodeSelect = vi.fn();
    renderDrawer(
      { ...RAVN_NODE, id: 'ravn-detached', hostId: 'host-missing' },
      {
        topology: { ...TOPOLOGY, nodes: TOPOLOGY.nodes.filter((n) => n.id !== 'host-missing') },
        onNodeSelect,
      },
    );
    fireEvent.click(screen.getByRole('button', { name: 'host-missing' }));
    expect(onNodeSelect).not.toHaveBeenCalled();
  });

  it('does not show Residents when realm has no children', () => {
    const emptyTopology: Topology = { nodes: [REALM_NODE], edges: [], timestamp: '' };
    renderDrawer(REALM_NODE, { topology: emptyTopology });
    expect(screen.queryByText('Residents')).toBeNull();
  });

  it('shows overflow text when a realm has more than twenty residents', () => {
    const residents = Array.from({ length: 21 }, (_, index) => ({
      id: `resident-${index}`,
      typeId: 'service',
      label: `resident-${index}`,
      parentId: REALM_NODE.id,
      status: 'healthy' as const,
    }));
    const crowdedTopology: Topology = {
      nodes: [REALM_NODE, ...residents],
      edges: [],
      timestamp: '2026-05-24T16:00:00Z',
    };

    renderDrawer(REALM_NODE, { topology: crowdedTopology });
    expect(screen.getByText('+1 more')).toBeInTheDocument();
    expect(screen.queryByTestId('resident-resident-20')).toBeNull();
  });

  it('calls onClose when drawer close button is clicked', () => {
    const { onClose } = renderDrawer(TING_NODE);
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('handles null topology gracefully (no residents)', () => {
    renderDrawer(REALM_NODE, { topology: null });
    expect(screen.queryByText('Residents')).toBeNull();
  });

  it('handles null registry gracefully (no rune, fallback label)', () => {
    renderDrawer(TING_NODE, { registry: null });
    expect(screen.getByText('ting')).toBeInTheDocument();
  });

  it('uses cluster parentId as the realm chip fallback when zone is missing', () => {
    renderDrawer({ ...CLUSTER_NODE, id: 'cluster-fallback', zone: undefined });
    expect(screen.getByText('realm · realm-asgard')).toBeInTheDocument();
  });

  it('shows placeholder activity timestamp when topology is missing', () => {
    renderDrawer(TING_NODE, { topology: null });
    expect(screen.getByText(/last tick · --:--/i)).toBeInTheDocument();
  });

  it('shows cluster and coordinator sections for container coordinators', () => {
    renderDrawer({
      ...RUN_NODE,
      id: 'run-coord',
      cluster: 'cluster-valaskjalf',
      role: 'coord',
      confidence: 0.87,
      flockId: 'long',
    });
    expect(screen.getByText('cluster')).toBeInTheDocument();
    expect(screen.getByText('cluster-valaskjalf')).toBeInTheDocument();
    expect(screen.getByText('Coordinator')).toBeInTheDocument();
    expect(screen.getByText('87%')).toBeInTheDocument();
    expect(screen.getByText('long')).toBeInTheDocument();
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
    const { onClose } = renderDrawer(TING_NODE);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('ignores non-Escape key presses', () => {
    const { onClose } = renderDrawer(TING_NODE);
    fireEvent.keyDown(document, { key: 'Enter' });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('does not add Escape listener when drawer is closed (node is null)', () => {
    const { onClose } = renderDrawer(null);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });
});
