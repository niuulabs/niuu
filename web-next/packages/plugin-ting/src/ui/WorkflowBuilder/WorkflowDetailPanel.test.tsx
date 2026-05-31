import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import {
  WorkflowDetailPanel,
  defaultTargetIdForType,
  issueTone,
  memberIssuesForPersona,
  personaGlyph,
  triggerEventOptions,
  uniquePersonaIds,
} from './WorkflowDetailPanel';
import type {
  Workflow,
  WorkflowNode,
  WorkflowResourceNode,
  WorkflowStageNode,
} from '../../domain/workflow';
import type { WorkflowIssue } from '../../domain/workflowValidation';
import type { PersonaEntry } from './LibraryPanel';
import type { WorkflowStageModelOption } from './useWorkflowBuilder';
import { EPHEMERAL_LOCAL_MOUNT_ID, type WorkflowRegistryMount } from './mimirRegistry';

const PERSONAS: PersonaEntry[] = [
  {
    id: 'planner',
    label: 'Planner',
    role: 'plan',
    consumes: ['code.requested'],
    produces: ['plan.created'],
  },
  {
    id: 'coder',
    label: 'Coder',
    role: 'build',
    consumes: ['plan.created'],
    produces: ['code.changed'],
  },
  {
    id: 'reviewer',
    label: 'Reviewer',
    role: 'verify',
    consumes: ['code.changed'],
    produces: ['review.completed'],
  },
];

const MODELS: WorkflowStageModelOption[] = [
  { id: 'claude-sonnet', label: 'Claude Sonnet' },
  { id: 'gpt-5', label: 'GPT-5' },
];

const REGISTRY_MOUNTS: WorkflowRegistryMount[] = [
  {
    id: 'shared-mimir',
    name: 'Shared Mimir',
    kind: 'remote',
    lifecycle: 'registered',
    role: 'shared',
    url: 'https://mimir.example',
    path: '/shared',
    categories: ['decision', 'reference'],
    authRef: 'mimir-secret',
    defaultReadPriority: 7,
    enabled: true,
    healthStatus: 'healthy',
    healthMessage: 'ok',
    desc: 'Shared team memory',
  },
  {
    id: 'domain-mimir',
    name: 'Domain Mimir',
    kind: 'remote',
    lifecycle: 'registered',
    role: 'domain',
    url: 'https://domain-mimir.example',
    path: '/domain',
    categories: ['playbook'],
    authRef: 'domain-secret',
    defaultReadPriority: 11,
    enabled: true,
    healthStatus: 'healthy',
    healthMessage: 'ok',
    desc: 'Domain-specific memory',
  },
];

const STAGE_NODE: WorkflowStageNode = {
  id: 'stage-1',
  kind: 'stage',
  label: 'Plan work',
  runId: null,
  personaIds: ['planner'],
  stageMembers: [{ personaId: 'planner', model: 'claude-sonnet', budget: 40 }],
  executionMode: 'parallel',
  maxConcurrent: 3,
  joinMode: 'all',
  position: { x: 120, y: 160 },
};

const RESOURCE_NODE: WorkflowResourceNode = {
  id: 'resource-1',
  kind: 'resource',
  label: 'Research Mimir',
  resourceType: 'mimir',
  bindingMode: 'registry',
  registryEntryId: 'shared-mimir',
  seedFromRegistryId: null,
  categories: ['decision'],
  path: '/shared',
  url: 'https://mimir.example',
  role: 'shared',
  authRef: 'mimir-secret',
  defaultReadPriority: 7,
  position: { x: 280, y: 120 },
};

const GATE_NODE: WorkflowNode = {
  id: 'gate-1',
  kind: 'gate',
  label: 'Approve plan',
  condition: 'Reviewer approves the plan',
  mode: 'human_approval',
  pendingBehavior: 'help_needed',
  approvalEvent: 'plan.approved',
  changesRequestedEvent: 'plan.changes_requested',
  instructions: 'Check the scope',
  autoForwardAfter: '30m',
  position: { x: 420, y: 120 },
};

const COND_NODE: WorkflowNode = {
  id: 'cond-1',
  kind: 'cond',
  label: 'Route outcome',
  predicate: 'score > 0.8',
  position: { x: 520, y: 120 },
};

const TRIGGER_NODE: WorkflowNode = {
  id: 'trigger-1',
  kind: 'trigger',
  label: 'Start',
  source: 'manual dispatch',
  dispatchEvent: 'code.requested',
  position: { x: 20, y: 60 },
};

const END_NODE: WorkflowNode = {
  id: 'end-1',
  kind: 'end',
  label: 'Done',
  position: { x: 620, y: 120 },
};

const STAGE_WITHOUT_MEMBERS: WorkflowStageNode = {
  id: 'stage-empty',
  kind: 'stage',
  label: 'Empty stage',
  runId: null,
  personaIds: [],
  stageMembers: [],
  position: { x: 180, y: 200 },
};

const EPHEMERAL_RESOURCE_NODE: WorkflowResourceNode = {
  ...RESOURCE_NODE,
  id: 'resource-ephemeral',
  label: 'Scratch Mimir',
  bindingMode: 'ephemeral_local',
  registryEntryId: EPHEMERAL_LOCAL_MOUNT_ID,
  seedFromRegistryId: null,
  path: null,
  url: null,
  role: 'local',
};

const BASE_WORKFLOW: Workflow = {
  id: '9c826fc4-cfb0-482b-9e8f-9db427ce9c11',
  name: 'Research Workflow',
  version: '0.1.0',
  description: 'Collect sources and ship a recommendation.',
  tags: ['research', 'review'],
  nodes: [
    {
      id: 'trigger-1',
      kind: 'trigger',
      label: 'Start',
      source: 'manual',
      dispatchEvent: 'code.requested',
      position: { x: 20, y: 60 },
    },
    STAGE_NODE,
    GATE_NODE,
    RESOURCE_NODE,
    { id: 'end-1', kind: 'end', label: 'Done', position: { x: 620, y: 120 } },
  ],
  edges: [
    {
      id: 'edge-1',
      source: 'trigger-1',
      target: 'stage-1',
      cp1: { x: 40, y: 0 },
      cp2: { x: -40, y: 0 },
    },
    {
      id: 'edge-2',
      source: 'stage-1',
      target: 'gate-1',
      cp1: { x: 60, y: 0 },
      cp2: { x: -60, y: 0 },
    },
  ],
  resourceBindings: [
    {
      id: 'binding-1',
      resourceNodeId: 'resource-1',
      targetType: 'workflow',
      targetId: '9c826fc4-cfb0-482b-9e8f-9db427ce9c11',
      access: 'read',
      writePrefixes: ['notes/'],
      readPriority: 7,
    },
  ],
};

const ISSUES: WorkflowIssue[] = [
  {
    kind: 'missing_model',
    nodeId: 'stage-1',
    message: 'Planner is missing a model',
    severity: 'error',
  },
];

function renderPanel(
  selectedNode: WorkflowNode | null,
  overrides: Partial<{
    workflow: Workflow;
    errorCount: number;
    warnCount: number;
    issues: WorkflowIssue[];
    personas: PersonaEntry[];
    models: WorkflowStageModelOption[];
    registryMounts: WorkflowRegistryMount[];
  }> = {},
) {
  const props = {
    workflow: overrides.workflow ?? BASE_WORKFLOW,
    selectedNode,
    errorCount: overrides.errorCount ?? 1,
    warnCount: overrides.warnCount ?? 2,
    issues: overrides.issues ?? ISSUES,
    personas: overrides.personas ?? PERSONAS,
    models: overrides.models ?? MODELS,
    registryMounts: overrides.registryMounts ?? REGISTRY_MOUNTS,
    onDeleteNode: vi.fn(),
    onUpdateNode: vi.fn(),
    onUpdateLabel: vi.fn(),
    onUpdateWorkflowMeta: vi.fn(),
    onAddPersona: vi.fn(),
    onReplacePersona: vi.fn(),
    onUpdatePersonaModel: vi.fn(),
    onUpdatePersonaBudget: vi.fn(),
    onRemovePersona: vi.fn(),
    onAddResourceBinding: vi.fn(),
    onUpdateResourceBinding: vi.fn(),
    onRemoveResourceBinding: vi.fn(),
  };

  return {
    ...render(<WorkflowDetailPanel {...props} />),
    props,
  };
}

describe('WorkflowDetailPanel', () => {
  it('renders workflow summary controls when no node is selected', () => {
    const { props } = renderPanel(null);

    fireEvent.change(screen.getByDisplayValue('Research Workflow'), {
      target: { value: 'Updated workflow' },
    });
    expect(props.onUpdateWorkflowMeta).toHaveBeenCalledWith({ name: 'Updated workflow' });

    fireEvent.change(screen.getByDisplayValue('Collect sources and ship a recommendation.'), {
      target: { value: 'Revised description' },
    });
    expect(props.onUpdateWorkflowMeta).toHaveBeenCalledWith({ description: 'Revised description' });

    fireEvent.change(screen.getByDisplayValue('0.1.0'), { target: { value: '1.0.0' } });
    expect(props.onUpdateWorkflowMeta).toHaveBeenCalledWith({ version: '1.0.0' });

    fireEvent.change(screen.getByPlaceholderText('research, campaign, review'), {
      target: { value: 'Alpha, Beta' },
    });
    expect(props.onUpdateWorkflowMeta).toHaveBeenCalledWith({ tags: ['alpha', 'beta'] });

    expect(screen.getByText('ERR 1')).toBeInTheDocument();
    expect(screen.getByText('WARN 2')).toBeInTheDocument();
  });

  it('updates stage configuration and validation details', () => {
    const { props } = renderPanel(STAGE_NODE);

    fireEvent.change(screen.getByDisplayValue('Plan work'), {
      target: { value: 'Plan scope' },
    });
    expect(props.onUpdateLabel).toHaveBeenCalledWith('stage-1', 'Plan scope');

    fireEvent.click(screen.getByRole('button', { name: 'sequential' }));
    expect(props.onUpdateNode).toHaveBeenCalledWith('stage-1', { executionMode: 'sequential' });

    fireEvent.change(screen.getByDisplayValue('3'), { target: { value: '0' } });
    expect(props.onUpdateNode).toHaveBeenCalledWith('stage-1', { maxConcurrent: 1 });

    fireEvent.click(screen.getByRole('button', { name: 'validate' }));
    expect(screen.getByText('missing_model')).toBeInTheDocument();
    expect(screen.getByText('Planner is missing a model')).toBeInTheDocument();
  });

  it('manages stage flock members and uses the default model when adding a persona', () => {
    const { props } = renderPanel(STAGE_NODE);

    fireEvent.click(screen.getByRole('button', { name: 'flock' }));
    const selects = screen.getAllByRole('combobox');

    fireEvent.change(screen.getByDisplayValue('40'), { target: { value: '55' } });
    expect(props.onUpdatePersonaBudget).toHaveBeenCalledWith('stage-1', 'planner', 55);

    fireEvent.change(selects[0]!, {
      target: { value: 'gpt-5' },
    });
    expect(props.onUpdatePersonaModel).toHaveBeenCalledWith('stage-1', 'planner', 'gpt-5');

    fireEvent.change(selects[1]!, {
      target: { value: 'coder' },
    });
    expect(props.onReplacePersona).toHaveBeenCalledWith(
      'stage-1',
      'planner',
      'coder',
      'claude-sonnet',
    );

    fireEvent.click(screen.getByRole('button', { name: '×' }));
    expect(props.onRemovePersona).toHaveBeenCalledWith('stage-1', 'planner');

    fireEvent.change(selects[2]!, { target: { value: 'reviewer' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add ravn' }));
    expect(props.onAddPersona).toHaveBeenCalledWith('stage-1', 'reviewer', 'claude-sonnet', 40);
  });

  it('updates resource bindings and supports ephemeral local mounts', () => {
    const { props } = renderPanel(RESOURCE_NODE);
    const registryMountSelect = screen
      .getByText('Registry mount')
      .parentElement?.querySelector('select') as HTMLSelectElement;
    const targetTypeSelect = screen
      .getByText('Target type')
      .parentElement?.querySelector('select') as HTMLSelectElement;
    const accessSelect = screen
      .getByText('Access')
      .parentElement?.querySelector('select') as HTMLSelectElement;

    fireEvent.change(screen.getByDisplayValue('Research Mimir'), {
      target: { value: 'Shared knowledge' },
    });
    expect(props.onUpdateLabel).toHaveBeenCalledWith('resource-1', 'Shared knowledge');

    fireEvent.change(screen.getByDisplayValue('decision'), {
      target: { value: 'decision, references ' },
    });
    expect(props.onUpdateNode).toHaveBeenCalledWith('resource-1', {
      categories: ['decision', 'references'],
    });

    fireEvent.change(registryMountSelect, {
      target: { value: 'domain-mimir' },
    });
    expect(props.onUpdateNode).toHaveBeenCalledWith('resource-1', {
      label: 'Domain Mimir',
      bindingMode: 'registry',
      registryEntryId: 'domain-mimir',
      categories: ['playbook'],
      path: '/domain',
      url: 'https://domain-mimir.example',
      role: 'domain',
      authRef: 'domain-secret',
      defaultReadPriority: 11,
    });

    fireEvent.click(screen.getByRole('button', { name: '+ binding' }));
    expect(props.onAddResourceBinding).toHaveBeenCalledWith('resource-1', {
      targetType: 'workflow',
      targetId: '9c826fc4-cfb0-482b-9e8f-9db427ce9c11',
      access: 'read',
      readPriority: 7,
    });

    fireEvent.change(targetTypeSelect, { target: { value: 'stage' } });
    expect(props.onUpdateResourceBinding).toHaveBeenCalledWith('binding-1', {
      targetType: 'stage',
      targetId: 'stage-1',
    });

    fireEvent.change(accessSelect, { target: { value: 'write' } });
    expect(props.onUpdateResourceBinding).toHaveBeenCalledWith('binding-1', {
      access: 'write',
    });

    fireEvent.change(screen.getByDisplayValue('7'), { target: { value: '12' } });
    expect(props.onUpdateResourceBinding).toHaveBeenCalledWith('binding-1', {
      readPriority: 12,
    });

    fireEvent.change(screen.getByPlaceholderText('project/, entity/'), {
      target: { value: 'docs/, reports/' },
    });
    expect(props.onUpdateResourceBinding).toHaveBeenCalledWith('binding-1', {
      writePrefixes: ['docs/', 'reports/'],
    });

    fireEvent.click(screen.getByRole('button', { name: 'ephemeral' }));
    expect(props.onUpdateNode).toHaveBeenCalledWith('resource-1', {
      bindingMode: 'ephemeral_local',
      registryEntryId: null,
      path: null,
      url: null,
      role: 'local',
      seedFromRegistryId: 'shared-mimir',
    });

    fireEvent.click(screen.getAllByRole('button', { name: '×' })[0]!);
    expect(props.onRemoveResourceBinding).toHaveBeenCalledWith('binding-1');
  });

  it('updates gate settings when a gate node is selected', () => {
    const { props } = renderPanel(GATE_NODE);

    fireEvent.change(screen.getByDisplayValue('Approve plan'), {
      target: { value: 'Approve scope' },
    });
    expect(props.onUpdateLabel).toHaveBeenCalledWith('gate-1', 'Approve scope');

    fireEvent.change(screen.getByLabelText('Gate mode'), {
      target: { value: 'automated_approval' },
    });
    expect(props.onUpdateNode).toHaveBeenCalledWith('gate-1', {
      mode: 'automated_approval',
    });

    fireEvent.change(screen.getByLabelText('Pending behavior'), {
      target: { value: 'notify_only' },
    });
    expect(props.onUpdateNode).toHaveBeenCalledWith('gate-1', {
      pendingBehavior: 'notify_only',
    });

    fireEvent.change(screen.getByDisplayValue('Reviewer approves the plan'), {
      target: { value: 'Review the scope and approve it' },
    });
    expect(props.onUpdateNode).toHaveBeenCalledWith('gate-1', {
      condition: 'Review the scope and approve it',
    });

    fireEvent.change(screen.getByDisplayValue('plan.approved'), {
      target: { value: ' scope.approved' },
    });
    expect(props.onUpdateNode).toHaveBeenCalledWith('gate-1', {
      approvalEvent: 'scope.approved',
    });

    fireEvent.change(screen.getByDisplayValue('plan.changes_requested'), {
      target: { value: ' scope.changes_requested' },
    });
    expect(props.onUpdateNode).toHaveBeenCalledWith('gate-1', {
      changesRequestedEvent: 'scope.changes_requested',
    });

    fireEvent.change(screen.getByDisplayValue('Check the scope'), {
      target: { value: 'Confirm architecture boundaries' },
    });
    expect(props.onUpdateNode).toHaveBeenCalledWith('gate-1', {
      instructions: 'Confirm architecture boundaries',
    });

    fireEvent.change(screen.getByDisplayValue('30m'), {
      target: { value: '45m' },
    });
    expect(props.onUpdateNode).toHaveBeenCalledWith('gate-1', {
      autoForwardAfter: '45m',
    });
  });

  it('updates condition, trigger, and end node details', () => {
    const cond = renderPanel(COND_NODE);
    fireEvent.change(screen.getByDisplayValue('Route outcome'), {
      target: { value: 'Route verdict' },
    });
    expect(cond.props.onUpdateLabel).toHaveBeenCalledWith('cond-1', 'Route verdict');

    fireEvent.change(screen.getByDisplayValue('score > 0.8'), {
      target: { value: 'score >= 0.9' },
    });
    expect(cond.props.onUpdateNode).toHaveBeenCalledWith('cond-1', {
      predicate: 'score >= 0.9',
    });

    cond.unmount();

    const trigger = renderPanel(TRIGGER_NODE);
    fireEvent.change(screen.getByDisplayValue('Start'), {
      target: { value: 'Kickoff' },
    });
    expect(trigger.props.onUpdateLabel).toHaveBeenCalledWith('trigger-1', 'Kickoff');

    fireEvent.change(screen.getByDisplayValue('code.requested'), {
      target: { value: 'plan.created' },
    });
    expect(trigger.props.onUpdateNode).toHaveBeenCalledWith('trigger-1', {
      dispatchEvent: 'plan.created',
    });

    fireEvent.change(screen.getByDisplayValue('manual dispatch'), {
      target: { value: 'workflow bootstrap' },
    });
    expect(trigger.props.onUpdateNode).toHaveBeenCalledWith('trigger-1', {
      source: 'workflow bootstrap',
    });

    trigger.unmount();

    const end = renderPanel(END_NODE);
    fireEvent.change(screen.getByDisplayValue('Done'), {
      target: { value: 'Completed' },
    });
    expect(end.props.onUpdateLabel).toHaveBeenCalledWith('end-1', 'Completed');
    expect(screen.getByText(/Terminal node/)).toBeInTheDocument();
  });

  it('shows stage empty states, fallback validation, and stage delete actions', () => {
    const workflow: Workflow = {
      ...BASE_WORKFLOW,
      nodes: [STAGE_WITHOUT_MEMBERS],
      edges: [],
    };
    const { props } = renderPanel(STAGE_WITHOUT_MEMBERS, {
      workflow,
      issues: [],
      models: [],
    });

    fireEvent.click(screen.getByRole('button', { name: 'flock' }));
    expect(screen.getByText('No ravns assigned yet.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add ravn' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'validate' }));
    expect(screen.getByText('No validation issues on this node.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'config' }));
    fireEvent.change(screen.getByDisplayValue('3'), { target: { value: '' } });
    expect(props.onUpdateNode).toHaveBeenCalledWith('stage-empty', { maxConcurrent: 1 });

    fireEvent.click(screen.getByRole('button', { name: 'Delete node' }));
    expect(props.onDeleteNode).toHaveBeenCalledWith('stage-empty');
  });

  it('covers resource fallback states, persona bindings, and delete actions', () => {
    const stageWithCoder: WorkflowStageNode = {
      ...STAGE_WITHOUT_MEMBERS,
      id: 'stage-coder',
      label: 'Code stage',
      personaIds: ['coder'],
      stageMembers: [{ personaId: 'coder', model: 'gpt-5', budget: 25 }],
    };
    const workflow: Workflow = {
      ...BASE_WORKFLOW,
      nodes: [TRIGGER_NODE, STAGE_NODE, stageWithCoder, EPHEMERAL_RESOURCE_NODE],
      resourceBindings: [
        {
          id: 'binding-persona',
          resourceNodeId: 'resource-ephemeral',
          targetType: 'persona',
          targetId: 'planner',
          access: 'read_write',
          writePrefixes: [],
          readPriority: 9,
        },
      ],
    };

    const { props, rerender } = renderPanel(EPHEMERAL_RESOURCE_NODE, {
      workflow,
    });

    expect(screen.getByPlaceholderText('scratch/, notes/')).toBeInTheDocument();
    expect(screen.getByText('Ephemeral local Mimir')).toBeInTheDocument();

    const seedSelect = screen
      .getByText('Seed from registry mount')
      .parentElement?.querySelector('select') as HTMLSelectElement;
    fireEvent.change(seedSelect, { target: { value: 'domain-mimir' } });
    expect(props.onUpdateNode).toHaveBeenCalledWith('resource-ephemeral', {
      seedFromRegistryId: 'domain-mimir',
    });

    fireEvent.click(screen.getByRole('button', { name: 'registry' }));
    expect(props.onUpdateNode).toHaveBeenCalledWith('resource-ephemeral', {
      bindingMode: 'registry',
      registryEntryId: null,
      path: null,
      url: null,
      role: 'local',
      seedFromRegistryId: null,
    });

    const targetTypeSelect = screen
      .getByText('Target type')
      .parentElement?.querySelector('select') as HTMLSelectElement;
    const targetSelect = screen
      .getByText('Target')
      .parentElement?.querySelector('select') as HTMLSelectElement;
    const accessSelect = screen
      .getByText('Access')
      .parentElement?.querySelector('select') as HTMLSelectElement;

    fireEvent.change(targetTypeSelect, { target: { value: 'persona' } });
    expect(props.onUpdateResourceBinding).toHaveBeenCalledWith('binding-persona', {
      targetType: 'persona',
      targetId: 'planner',
    });

    fireEvent.change(targetSelect, { target: { value: 'coder' } });
    expect(props.onUpdateResourceBinding).toHaveBeenCalledWith('binding-persona', {
      targetId: 'coder',
    });

    fireEvent.change(accessSelect, { target: { value: 'read_write' } });
    expect(props.onUpdateResourceBinding).toHaveBeenCalledWith('binding-persona', {
      access: 'read_write',
    });

    fireEvent.change(screen.getByDisplayValue('9'), { target: { value: '' } });
    expect(props.onUpdateResourceBinding).toHaveBeenCalledWith('binding-persona', {
      readPriority: 0,
    });

    fireEvent.click(screen.getByRole('button', { name: 'Delete node' }));
    expect(props.onDeleteNode).toHaveBeenCalledWith('resource-ephemeral');

    const registryWithoutBinding: Workflow = {
      ...BASE_WORKFLOW,
      nodes: [{ ...RESOURCE_NODE, registryEntryId: null }],
      resourceBindings: [],
    };

    rerender(
      <WorkflowDetailPanel
        workflow={registryWithoutBinding}
        selectedNode={{ ...RESOURCE_NODE, registryEntryId: null }}
        errorCount={1}
        warnCount={2}
        issues={ISSUES}
        personas={PERSONAS}
        models={MODELS}
        registryMounts={REGISTRY_MOUNTS}
        onDeleteNode={props.onDeleteNode}
        onUpdateNode={props.onUpdateNode}
        onUpdateLabel={props.onUpdateLabel}
        onUpdateWorkflowMeta={props.onUpdateWorkflowMeta}
        onAddPersona={props.onAddPersona}
        onReplacePersona={props.onReplacePersona}
        onUpdatePersonaModel={props.onUpdatePersonaModel}
        onUpdatePersonaBudget={props.onUpdatePersonaBudget}
        onRemovePersona={props.onRemovePersona}
        onAddResourceBinding={props.onAddResourceBinding}
        onUpdateResourceBinding={props.onUpdateResourceBinding}
        onRemoveResourceBinding={props.onRemoveResourceBinding}
      />,
    );

    expect(screen.getByText(/No workflow bindings yet/)).toBeInTheDocument();

    const registryMountSelect = screen
      .getByText('Registry mount')
      .parentElement?.querySelector('select') as HTMLSelectElement;
    fireEvent.change(registryMountSelect, { target: { value: '' } });
    expect(props.onUpdateNode).toHaveBeenCalledWith('resource-1', {
      registryEntryId: null,
    });
  });

  it('includes custom trigger events in the dispatch options', () => {
    const customTrigger: WorkflowNode = {
      ...TRIGGER_NODE,
      dispatchEvent: 'custom.signal',
    };

    renderPanel(customTrigger);
    expect(screen.getByRole('option', { name: 'custom.signal' })).toBeInTheDocument();
  });

  it('updates stage-target bindings when the binding already targets a stage', () => {
    const workflow: Workflow = {
      ...BASE_WORKFLOW,
      nodes: [STAGE_NODE, { ...STAGE_WITHOUT_MEMBERS, id: 'stage-2', label: 'Review stage' }],
      resourceBindings: [
        {
          id: 'binding-stage',
          resourceNodeId: 'resource-1',
          targetType: 'stage',
          targetId: 'stage-1',
          access: 'read',
          writePrefixes: [],
          readPriority: 5,
        },
      ],
    };

    const { props } = renderPanel(RESOURCE_NODE, { workflow });
    const targetSelect = screen
      .getByText('Target')
      .parentElement?.querySelector('select') as HTMLSelectElement;

    fireEvent.change(targetSelect, { target: { value: 'stage-2' } });
    expect(props.onUpdateResourceBinding).toHaveBeenCalledWith('binding-stage', {
      targetId: 'stage-2',
    });
  });

  it('covers exported detail-panel helpers', () => {
    expect(issueTone('error')).toContain('niuu:text-critical');
    expect(issueTone('warning')).toContain('niuu:text-status-amber');

    expect(personaGlyph('plan')).toBe('D');
    expect(personaGlyph('build')).toBe('C');
    expect(personaGlyph('verify')).toBe('V');
    expect(personaGlyph('gate')).toBe('I');
    expect(personaGlyph('unknown')).toBe('•');

    expect(memberIssuesForPersona(ISSUES, undefined)).toEqual([]);
    expect(
      memberIssuesForPersona(
        [
          {
            kind: 'consume_issue',
            nodeId: 'stage-1',
            message: 'planner cannot consume CODE.REQUESTED yet',
            severity: 'warning',
          },
          {
            kind: 'produce_issue',
            nodeId: 'stage-1',
            message: 'PLAN.CREATED is missing downstream handling',
            severity: 'error',
          },
        ],
        PERSONAS[0],
      ),
    ).toHaveLength(2);

    expect(triggerEventOptions(PERSONAS, 'custom.signal')).toEqual([
      'code.changed',
      'code.requested',
      'custom.signal',
      'plan.created',
    ]);

    const helperWorkflow: Workflow = {
      ...BASE_WORKFLOW,
      nodes: [
        STAGE_NODE,
        {
          ...STAGE_WITHOUT_MEMBERS,
          id: 'stage-helper',
          personaIds: ['coder', 'planner'],
          stageMembers: [
            { personaId: 'coder', model: 'gpt-5', budget: 20 },
            { personaId: 'planner', model: 'claude-sonnet', budget: 40 },
          ],
        },
      ],
    };

    expect(uniquePersonaIds(helperWorkflow)).toEqual(['planner', 'coder']);
    expect(defaultTargetIdForType(helperWorkflow, 'workflow')).toBe(helperWorkflow.id);
    expect(defaultTargetIdForType(helperWorkflow, 'stage')).toBe('stage-1');
    expect(defaultTargetIdForType(helperWorkflow, 'persona')).toBe('planner');

    const emptyWorkflow: Workflow = {
      ...BASE_WORKFLOW,
      nodes: [],
      edges: [],
      resourceBindings: [],
    };

    expect(defaultTargetIdForType(emptyWorkflow, 'stage')).toBe('');
    expect(defaultTargetIdForType(emptyWorkflow, 'persona')).toBe('');
  });
});
