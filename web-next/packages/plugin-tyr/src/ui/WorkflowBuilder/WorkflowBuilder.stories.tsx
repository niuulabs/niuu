import type { Meta, StoryObj } from '@storybook/react';
import { WorkflowBuilder } from './WorkflowBuilder';
import { GraphView } from './GraphView';
import { PipelineView } from './PipelineView';
import { YamlView } from './YamlView';
import { ValidationPanel } from './ValidationPanel';
import { LibraryPanel, DEFAULT_PERSONAS } from './LibraryPanel';
import type { Workflow } from '../../domain/workflow';

// ---------------------------------------------------------------------------
// Shared workflow fixtures
// ---------------------------------------------------------------------------

const simpleWorkflow: Workflow = {
  id: '00000000-0000-0000-0000-000000000001',
  name: 'Auth Rewrite Workflow',
  nodes: [
    {
      id: 'stage-1',
      kind: 'stage',
      label: 'Set up CI',
      raidId: 'raid-123',
      personaIds: ['persona-plan', 'persona-build'],
      position: { x: 80, y: 140 },
    },
    {
      id: 'gate-1',
      kind: 'gate',
      label: 'QA gate',
      condition: 'all tests pass',
      position: { x: 280, y: 128 },
    },
    {
      id: 'cond-1',
      kind: 'cond',
      label: 'Green?',
      predicate: 'ci.exitCode === 0',
      position: { x: 420, y: 135 },
    },
    {
      id: 'stage-2',
      kind: 'stage',
      label: 'Deploy',
      raidId: null,
      personaIds: ['persona-ship'],
      position: { x: 560, y: 140 },
    },
  ],
  edges: [
    { id: 'e1', source: 'stage-1', target: 'gate-1', cp1: { x: 80, y: 0 }, cp2: { x: -80, y: 0 } },
    { id: 'e2', source: 'gate-1', target: 'cond-1', cp1: { x: 60, y: 0 }, cp2: { x: -60, y: 0 } },
    { id: 'e3', source: 'cond-1', target: 'stage-2', cp1: { x: 60, y: 0 }, cp2: { x: -60, y: 0 } },
  ],
};

const cyclicWorkflow: Workflow = {
  id: '00000000-0000-0000-0000-000000000002',
  name: 'Cyclic Workflow (invalid)',
  nodes: [
    {
      id: 'stage-a',
      kind: 'stage',
      label: 'Stage A',
      raidId: null,
      personaIds: [],
      position: { x: 100, y: 140 },
    },
    {
      id: 'stage-b',
      kind: 'stage',
      label: 'Stage B',
      raidId: null,
      personaIds: [],
      position: { x: 300, y: 140 },
    },
  ],
  edges: [
    { id: 'e1', source: 'stage-a', target: 'stage-b', cp1: { x: 80, y: 0 }, cp2: { x: -80, y: 0 } },
    {
      id: 'e2',
      source: 'stage-b',
      target: 'stage-a',
      cp1: { x: -80, y: 40 },
      cp2: { x: 80, y: 40 },
    },
  ],
};

const emptyWorkflow: Workflow = {
  id: '00000000-0000-0000-0000-000000000003',
  name: 'Empty Workflow',
  nodes: [],
  edges: [],
};

// ---------------------------------------------------------------------------
// WorkflowBuilder — main three-view editor
// ---------------------------------------------------------------------------

const meta: Meta<typeof WorkflowBuilder> = {
  title: 'Tyr/WorkflowBuilder',
  component: WorkflowBuilder,
  parameters: {
    layout: 'fullscreen',
  },
};

export default meta;
type Story = StoryObj<typeof WorkflowBuilder>;

/** Full editor with a realistic workflow — starts on Graph view. */
export const Default: Story = {
  args: {
    initialWorkflow: simpleWorkflow,
    onSave: (wf) => console.log('Saved:', wf),
  },
};

/** Empty canvas — no nodes yet. */
export const EmptyWorkflow: Story = {
  args: {
    initialWorkflow: emptyWorkflow,
  },
};

/** Read-only (no onSave) mode — hides the Save button. */
export const ReadOnly: Story = {
  args: {
    initialWorkflow: simpleWorkflow,
  },
};

// ---------------------------------------------------------------------------
// GraphView — standalone stories per node kind
// ---------------------------------------------------------------------------

function noop() {}

export const graphMeta: Meta<typeof GraphView> = {
  title: 'Tyr/WorkflowBuilder/GraphView',
  component: GraphView,
  parameters: { layout: 'fullscreen' },
};
export const GraphStageNodes: StoryObj<typeof GraphView> = {
  render: () => (
    <GraphView
      nodes={[
        {
          id: 's1',
          kind: 'stage',
          label: 'Stage A',
          raidId: null,
          personaIds: ['p1'],
          position: { x: 80, y: 100 },
        },
        {
          id: 's2',
          kind: 'stage',
          label: 'Stage B',
          raidId: null,
          personaIds: [],
          position: { x: 280, y: 100 },
        },
      ]}
      edges={[
        { id: 'e1', source: 's1', target: 's2', cp1: { x: 80, y: 0 }, cp2: { x: -80, y: 0 } },
      ]}
      selectedNodeId={null}
      connectingFromId={null}
      onSelectNode={noop}
      onInspectNode={noop}
      onAddNode={noop}
      onDeleteNode={noop}
      onMoveNode={noop}
      onStartConnect={noop}
      onCancelConnect={noop}
      onCompleteConnect={noop}
    />
  ),
};

export const GraphGateNode: StoryObj<typeof GraphView> = {
  render: () => (
    <GraphView
      nodes={[
        {
          id: 'g1',
          kind: 'gate',
          label: 'QA gate',
          condition: 'all pass',
          position: { x: 200, y: 120 },
        },
      ]}
      edges={[]}
      selectedNodeId={null}
      connectingFromId={null}
      onSelectNode={noop}
      onInspectNode={noop}
      onAddNode={noop}
      onDeleteNode={noop}
      onMoveNode={noop}
      onStartConnect={noop}
      onCancelConnect={noop}
      onCompleteConnect={noop}
    />
  ),
};

export const GraphCondNode: StoryObj<typeof GraphView> = {
  render: () => (
    <GraphView
      nodes={[
        {
          id: 'c1',
          kind: 'cond',
          label: 'Green?',
          predicate: 'ci.ok',
          position: { x: 200, y: 120 },
        },
      ]}
      edges={[]}
      selectedNodeId={null}
      connectingFromId={null}
      onSelectNode={noop}
      onInspectNode={noop}
      onAddNode={noop}
      onDeleteNode={noop}
      onMoveNode={noop}
      onStartConnect={noop}
      onCancelConnect={noop}
      onCompleteConnect={noop}
    />
  ),
};

export const GraphMixedNodes: StoryObj<typeof GraphView> = {
  render: () => (
    <GraphView
      nodes={simpleWorkflow.nodes}
      edges={simpleWorkflow.edges}
      selectedNodeId="stage-1"
      connectingFromId={null}
      onSelectNode={noop}
      onInspectNode={noop}
      onAddNode={noop}
      onDeleteNode={noop}
      onMoveNode={noop}
      onStartConnect={noop}
      onCancelConnect={noop}
      onCompleteConnect={noop}
    />
  ),
};

// ---------------------------------------------------------------------------
// PipelineView
// ---------------------------------------------------------------------------

export const PipelineLayout: StoryObj<typeof PipelineView> = {
  render: () => <PipelineView nodes={simpleWorkflow.nodes} edges={simpleWorkflow.edges} />,
};

export const PipelineWithCycle: StoryObj<typeof PipelineView> = {
  render: () => <PipelineView nodes={cyclicWorkflow.nodes} edges={cyclicWorkflow.edges} />,
};

export const PipelineEmpty: StoryObj<typeof PipelineView> = {
  render: () => <PipelineView nodes={[]} edges={[]} />,
};

// ---------------------------------------------------------------------------
// YamlView
// ---------------------------------------------------------------------------

export const YamlWorkflow: StoryObj<typeof YamlView> = {
  render: () => <YamlView workflow={simpleWorkflow} />,
};

export const YamlEmpty: StoryObj<typeof YamlView> = {
  render: () => <YamlView workflow={emptyWorkflow} />,
};

// ---------------------------------------------------------------------------
// ValidationPanel states
// ---------------------------------------------------------------------------

export const ValidationNoIssues: StoryObj<typeof ValidationPanel> = {
  render: () => (
    <div style={{ position: 'relative', height: 300, background: 'var(--color-bg-primary)' }}>
      <ValidationPanel workflow={simpleWorkflow} onSelectNode={noop} />
    </div>
  ),
};

export const ValidationWithCycle: StoryObj<typeof ValidationPanel> = {
  render: () => (
    <div style={{ position: 'relative', height: 300, background: 'var(--color-bg-primary)' }}>
      <ValidationPanel
        workflow={cyclicWorkflow}
        onSelectNode={(id) => console.log('Select:', id)}
      />
    </div>
  ),
};

// ---------------------------------------------------------------------------
// LibraryPanel
// ---------------------------------------------------------------------------

export const LibraryDefault: StoryObj<typeof LibraryPanel> = {
  render: () => (
    <div style={{ width: 160, height: 400, background: 'var(--color-bg-primary)' }}>
      <LibraryPanel personas={DEFAULT_PERSONAS} />
    </div>
  ),
};
