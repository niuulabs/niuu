import type { Meta, StoryObj } from '@storybook/react';
import { TopologyCanvas } from './TopologyCanvas';
import type { Topology } from '../../domain';

// ── Mock data ─────────────────────────────────────────────────────────────────

/** Topology seed that exercises all 5 edge kinds and major node types. */
const FULL_TOPOLOGY: Topology = {
  timestamp: '2026-04-19T00:00:00Z',
  nodes: [
    // Core
    { id: 'mimir-0', typeId: 'mimir', label: 'mímir-0', parentId: null, status: 'healthy' },
    // Realms
    { id: 'realm-asgard', typeId: 'realm', label: 'asgard', parentId: null, status: 'healthy' },
    { id: 'realm-vanaheim', typeId: 'realm', label: 'vanaheim', parentId: null, status: 'healthy' },
    { id: 'realm-alfheim', typeId: 'realm', label: 'alfheim', parentId: null, status: 'healthy' },
    { id: 'realm-midgard', typeId: 'realm', label: 'midgard', parentId: null, status: 'healthy' },
    {
      id: 'realm-bifrost',
      typeId: 'realm',
      label: 'bifröst-edge',
      parentId: null,
      status: 'healthy',
    },
    // Clusters
    {
      id: 'cluster-vk',
      typeId: 'cluster',
      label: 'valaskjálf',
      parentId: 'realm-asgard',
      status: 'healthy',
    },
    {
      id: 'cluster-vh',
      typeId: 'cluster',
      label: 'valhalla',
      parentId: 'realm-asgard',
      status: 'healthy',
    },
    {
      id: 'cluster-gl',
      typeId: 'cluster',
      label: 'glitnir',
      parentId: 'realm-alfheim',
      status: 'healthy',
    },
    // Hosts
    {
      id: 'host-tanngrisnir',
      typeId: 'host',
      label: 'tanngrisnir',
      parentId: 'realm-asgard',
      status: 'healthy',
    },
    {
      id: 'host-saga',
      typeId: 'host',
      label: 'saga',
      parentId: 'realm-vanaheim',
      status: 'healthy',
    },
    // Coordinators
    { id: 'ting-0', typeId: 'ting', label: 'ting-0', parentId: 'cluster-vk', status: 'healthy' },
    {
      id: 'bifrost-0',
      typeId: 'bifrost',
      label: 'bifröst-0',
      parentId: 'cluster-vk',
      status: 'healthy',
    },
    {
      id: 'volundr-0',
      typeId: 'volundr',
      label: 'völundr-0',
      parentId: 'cluster-vh',
      status: 'healthy',
    },
    // Agents
    {
      id: 'ravn-huginn',
      typeId: 'ravn_long',
      label: 'huginn',
      parentId: 'host-tanngrisnir',
      status: 'healthy',
    },
    {
      id: 'ravn-muninn',
      typeId: 'ravn_long',
      label: 'muninn',
      parentId: 'host-tanngrisnir',
      status: 'idle',
    },
    { id: 'skuld-0', typeId: 'skuld', label: 'skuld-0', parentId: 'cluster-vk', status: 'healthy' },
    {
      id: 'valk-brynhildr',
      typeId: 'valkyrie',
      label: 'brynhildr',
      parentId: 'cluster-vk',
      status: 'healthy',
    },
    // Run
    {
      id: 'run-omega',
      typeId: 'run',
      label: 'run-omega',
      parentId: 'cluster-vk',
      status: 'observing',
    },
    {
      id: 'ravn-coord',
      typeId: 'ravn_run',
      label: 'coord-1',
      parentId: 'run-omega',
      status: 'healthy',
    },
    {
      id: 'ravn-rev',
      typeId: 'ravn_run',
      label: 'reviewer-1',
      parentId: 'run-omega',
      status: 'healthy',
    },
    // Services
    {
      id: 'svc-grafana',
      typeId: 'service',
      label: 'grafana',
      parentId: 'cluster-gl',
      status: 'healthy',
    },
    {
      id: 'svc-pg',
      typeId: 'service',
      label: 'postgresql',
      parentId: 'cluster-vk',
      status: 'healthy',
    },
    // Models
    {
      id: 'model-claude',
      typeId: 'model',
      label: 'claude-sonnet',
      parentId: 'realm-bifrost',
      status: 'healthy',
    },
    {
      id: 'model-gpt4',
      typeId: 'model',
      label: 'gpt-4o',
      parentId: 'realm-bifrost',
      status: 'healthy',
    },
    {
      id: 'model-ollama',
      typeId: 'model',
      label: 'ollama',
      parentId: 'cluster-vk',
      status: 'healthy',
    },
    // Devices
    {
      id: 'printer-gungnir',
      typeId: 'printer',
      label: 'gungnir',
      parentId: 'realm-midgard',
      status: 'healthy',
    },
    {
      id: 'vaettir-office',
      typeId: 'vaettir',
      label: 'chatterbox/office',
      parentId: 'realm-midgard',
      status: 'healthy',
    },
    {
      id: 'beacon-office',
      typeId: 'beacon',
      label: 'espresense/office',
      parentId: 'realm-midgard',
      status: 'healthy',
    },
  ],
  edges: [
    // solid: coordinator links
    { id: 'e-ting-volundr', sourceId: 'ting-0', targetId: 'volundr-0', kind: 'solid' },
    // dashed-anim: run dispatch
    { id: 'e-ting-run', sourceId: 'ting-0', targetId: 'run-omega', kind: 'dashed-anim' },
    // dashed-long: async memory access
    { id: 'e-huginn-mimir', sourceId: 'ravn-huginn', targetId: 'mimir-0', kind: 'dashed-long' },
    { id: 'e-muninn-mimir', sourceId: 'ravn-muninn', targetId: 'mimir-0', kind: 'dashed-long' },
    // soft: reference
    { id: 'e-bifrost-mimir', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'soft' },
    // run: inter-raven cohesion
    { id: 'e-run-coord', sourceId: 'run-omega', targetId: 'ravn-coord', kind: 'run' },
    { id: 'e-run-rev', sourceId: 'run-omega', targetId: 'ravn-rev', kind: 'run' },
  ],
};

/** Minimal topology — just Mímir and one realm. Useful for minimap story. */
const MINIMAL_TOPOLOGY: Topology = {
  timestamp: '2026-04-19T00:00:00Z',
  nodes: [
    { id: 'mimir-0', typeId: 'mimir', label: 'mímir-0', parentId: null, status: 'healthy' },
    { id: 'realm-asgard', typeId: 'realm', label: 'asgard', parentId: null, status: 'healthy' },
    {
      id: 'cluster-vk',
      typeId: 'cluster',
      label: 'valaskjálf',
      parentId: 'realm-asgard',
      status: 'healthy',
    },
    { id: 'ting-0', typeId: 'ting', label: 'ting-0', parentId: 'cluster-vk', status: 'healthy' },
  ],
  edges: [{ id: 'e1', sourceId: 'ting-0', targetId: 'mimir-0', kind: 'soft' }],
};

// ── Meta ──────────────────────────────────────────────────────────────────────

const meta: Meta<typeof TopologyCanvas> = {
  title: 'Observatory/TopologyCanvas',
  component: TopologyCanvas,
  parameters: {
    layout: 'fullscreen',
    backgrounds: {
      default: 'dark',
      values: [{ name: 'dark', value: '#09090b' }],
    },
  },
  decorators: [
    (Story) => (
      <div style={{ width: '100vw', height: '100vh', background: '#09090b' }}>
        <Story />
      </div>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof TopologyCanvas>;

// ── Stories ───────────────────────────────────────────────────────────────────

/** Full topology: all entity kinds, all 5 edge types, minimap active. */
export const WithMockTopology: Story = {
  name: 'With Mock Topology',
  args: {
    topology: FULL_TOPOLOGY,
    showMinimap: true,
  },
};

/** Just the canvas minimap panel — navigate by clicking the minimap. */
export const MinimapNavigation: Story = {
  name: 'Minimap Navigation',
  args: {
    topology: FULL_TOPOLOGY,
    showMinimap: true,
  },
  parameters: {
    docs: {
      description: {
        story:
          'The minimap in the bottom-right shows the full world at a glance. ' +
          'Click anywhere on the minimap to pan the main camera to that point.',
      },
    },
  },
};

/** Canvas without the minimap overlay — full viewport for the main view. */
export const NoMinimap: Story = {
  name: 'No Minimap',
  args: {
    topology: FULL_TOPOLOGY,
    showMinimap: false,
  },
};

/** Minimal topology — Mímir, one realm, one cluster, one Ting. */
export const MinimalTopology: Story = {
  name: 'Minimal Topology',
  args: {
    topology: MINIMAL_TOPOLOGY,
    showMinimap: true,
  },
};

/** Null topology — canvas renders in connecting state, no data. */
export const Connecting: Story = {
  name: 'Connecting (null topology)',
  args: {
    topology: null,
    showMinimap: true,
  },
};

/** All 5 edge kinds highlighted — click nodes to inspect connection taxonomy. */
export const AllEdgeKinds: Story = {
  name: 'All 5 Edge Kinds',
  args: {
    topology: {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'mimir-0', typeId: 'mimir', label: 'mímir', parentId: null, status: 'healthy' },
        { id: 'realm-a', typeId: 'realm', label: 'asgard', parentId: null, status: 'healthy' },
        {
          id: 'cluster-vk',
          typeId: 'cluster',
          label: 'cluster',
          parentId: 'realm-a',
          status: 'healthy',
        },
        { id: 'ting-0', typeId: 'ting', label: 'ting', parentId: 'cluster-vk', status: 'healthy' },
        {
          id: 'bifrost-0',
          typeId: 'bifrost',
          label: 'bifröst',
          parentId: 'cluster-vk',
          status: 'healthy',
        },
        {
          id: 'volundr-0',
          typeId: 'volundr',
          label: 'völundr',
          parentId: 'cluster-vk',
          status: 'healthy',
        },
        {
          id: 'ravn-a',
          typeId: 'ravn_long',
          label: 'huginn',
          parentId: 'cluster-vk',
          status: 'healthy',
        },
        {
          id: 'run-0',
          typeId: 'run',
          label: 'run',
          parentId: 'cluster-vk',
          status: 'observing',
        },
        {
          id: 'ravn-run-a',
          typeId: 'ravn_run',
          label: 'coord',
          parentId: 'run-0',
          status: 'healthy',
        },
      ],
      edges: [
        { id: 'e-solid', sourceId: 'ting-0', targetId: 'volundr-0', kind: 'solid' },
        { id: 'e-dashed-anim', sourceId: 'ting-0', targetId: 'run-0', kind: 'dashed-anim' },
        { id: 'e-dashed-long', sourceId: 'ravn-a', targetId: 'mimir-0', kind: 'dashed-long' },
        { id: 'e-soft', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'soft' },
        { id: 'e-run', sourceId: 'run-0', targetId: 'ravn-run-a', kind: 'run' },
      ],
    } satisfies Topology,
    showMinimap: true,
  },
  parameters: {
    docs: {
      description: {
        story:
          'Demonstrates all five connection-line styles:\n' +
          '- **solid** — Ting → Völundr coordinator link (cyan)\n' +
          '- **dashed-anim** — Ting → run dispatch (animated blue)\n' +
          '- **dashed-long** — raven → Mímir async memory (long dash)\n' +
          '- **soft** — Bifröst → Mímir soft reference (translucent)\n' +
          '- **run** — inter-raven cohesion within run (frost)',
      },
    },
  },
};
