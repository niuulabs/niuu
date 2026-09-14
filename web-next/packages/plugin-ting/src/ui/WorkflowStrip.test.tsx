import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Workflow } from '../domain/workflow';
import { WorkflowStrip } from './WorkflowStrip';

const workflow: Workflow = {
  id: '00000000-0000-0000-0000-000000000001',
  name: 'Build and ship',
  nodes: [
    {
      id: 'build',
      kind: 'stage',
      label: 'Build it',
      runId: null,
      personaIds: [],
      stageMembers: [
        {
          personaId: 'coder',
          model: 'claude-fable-5',
          budget: 40,
          consumesEventTypes: [],
          eventFilters: {},
        },
      ],
      position: { x: 0, y: 0 },
    },
    {
      id: 'plan-gate',
      kind: 'gate',
      label: 'the plan',
      condition: '',
      mode: 'human_approval',
      position: { x: 0, y: 0 },
    },
    {
      id: 'test-gate',
      kind: 'gate',
      label: 'automatic',
      condition: '',
      mode: 'automated_approval',
      position: { x: 0, y: 0 },
    },
    { id: 'done', kind: 'end', label: 'Done', position: { x: 0, y: 0 } },
  ],
  edges: [
    { id: 'e1', source: 'build', target: 'plan-gate', cp1: { x: 0, y: 0 }, cp2: { x: 0, y: 0 } },
    {
      id: 'e2',
      source: 'plan-gate',
      target: 'test-gate',
      cp1: { x: 0, y: 0 },
      cp2: { x: 0, y: 0 },
    },
    { id: 'e3', source: 'test-gate', target: 'done', cp1: { x: 0, y: 0 }, cp2: { x: 0, y: 0 } },
  ],
};

describe('WorkflowStrip', () => {
  it('tells the reader nothing runs yet when the workflow is empty', () => {
    render(<WorkflowStrip nodes={[]} edges={[]} />);
    expect(screen.getByTestId('workflow-strip')).toHaveTextContent('no stages yet');
  });

  it('renders every node with its kind and the caption', () => {
    render(<WorkflowStrip nodes={workflow.nodes} edges={workflow.edges} />);

    expect(screen.getByTestId('workflow-strip-node-build')).toHaveAttribute('data-kind', 'stage');
    expect(screen.getByTestId('workflow-strip-node-build')).toHaveTextContent('coder');
    expect(screen.getByTestId('workflow-strip-node-plan-gate')).toHaveAttribute(
      'data-kind',
      'gate',
    );
    expect(screen.getByTestId('workflow-strip-node-done')).toHaveAttribute('data-kind', 'end');
    expect(
      screen.getByText('Diamonds are where it stops and waits for you. Boxes run on their own.'),
    ).toBeInTheDocument();
  });

  it('labels human gates "You approve" and automated ones "Automatic"', () => {
    render(<WorkflowStrip nodes={workflow.nodes} edges={workflow.edges} />);
    expect(screen.getByTestId('workflow-strip-node-plan-gate')).toHaveTextContent('You approve');
    expect(screen.getByTestId('workflow-strip-node-test-gate')).toHaveTextContent('Automatic');
  });

  it('says when a stage has no persona', () => {
    render(
      <WorkflowStrip
        nodes={[
          {
            id: 'bare',
            kind: 'stage',
            label: 'Bare',
            runId: null,
            personaIds: [],
            position: { x: 0, y: 0 },
          },
        ]}
        edges={[]}
      />,
    );
    expect(screen.getByTestId('workflow-strip-node-bare')).toHaveTextContent('no persona yet');
  });

  it('condenses long persona lists', () => {
    render(
      <WorkflowStrip
        nodes={[
          {
            id: 'crowd',
            kind: 'stage',
            label: 'Crowd',
            runId: null,
            personaIds: ['a', 'b', 'c', 'd'],
            position: { x: 0, y: 0 },
          },
        ]}
        edges={[]}
      />,
    );
    expect(screen.getByTestId('workflow-strip-node-crowd')).toHaveTextContent('a · b +2');
  });

  it('describes triggers, conditions and gates, and leaves resource bindings out', () => {
    render(
      <WorkflowStrip
        nodes={[
          { id: 'kick', kind: 'trigger', label: 'Kick off', position: { x: 0, y: 0 } },
          {
            id: 'branch',
            kind: 'cond',
            label: 'Branch',
            predicate: '',
            position: { x: 0, y: 0 },
          },
          { id: 'mimir', kind: 'resource', label: 'Mimir', position: { x: 0, y: 0 } },
          {
            id: 'default-gate',
            kind: 'gate',
            label: 'Sign-off',
            condition: '',
            position: { x: 0, y: 0 },
          },
        ]}
        edges={[]}
      />,
    );
    expect(screen.getByTestId('workflow-strip-node-kick')).toHaveTextContent('manual dispatch');
    expect(screen.getByTestId('workflow-strip-node-branch')).toHaveTextContent('condition');
    expect(screen.queryByTestId('workflow-strip-node-mimir')).toBeNull();
    // No mode means human approval, so it waits.
    expect(screen.getByTestId('workflow-strip-node-default-gate')).toHaveTextContent('You approve');
  });

  it('uses the trigger source and the condition predicate when they are set', () => {
    render(
      <WorkflowStrip
        nodes={[
          {
            id: 'kick',
            kind: 'trigger',
            label: 'Kick off',
            source: 'tracker issue',
            position: { x: 0, y: 0 },
          },
          {
            id: 'branch',
            kind: 'cond',
            label: 'Branch',
            predicate: 'tests.passed',
            position: { x: 0, y: 0 },
          },
        ]}
        edges={[]}
      />,
    );
    expect(screen.getByTestId('workflow-strip-node-kick')).toHaveTextContent('tracker issue');
    expect(screen.getByTestId('workflow-strip-node-branch')).toHaveTextContent('tests.passed');
  });

  it('surfaces nodes that a cycle keeps out of the layers', () => {
    render(
      <WorkflowStrip
        nodes={[
          {
            id: 'left',
            kind: 'stage',
            label: 'Left',
            runId: null,
            personaIds: [],
            position: { x: 0, y: 0 },
          },
          {
            id: 'right',
            kind: 'stage',
            label: 'Right',
            runId: null,
            personaIds: [],
            position: { x: 0, y: 0 },
          },
        ]}
        edges={[
          { id: 'a', source: 'left', target: 'right', cp1: { x: 0, y: 0 }, cp2: { x: 0, y: 0 } },
          { id: 'b', source: 'right', target: 'left', cp1: { x: 0, y: 0 }, cp2: { x: 0, y: 0 } },
        ]}
      />,
    );
    expect(screen.getByTestId('workflow-strip-loops')).toHaveTextContent(/loops back/i);
    expect(screen.getAllByTestId('workflow-strip-cycle')).toHaveLength(2);
  });

  it('shows a gate that a cycle left out as a diamond too', () => {
    render(
      <WorkflowStrip
        nodes={[
          {
            id: 'loop-gate',
            kind: 'gate',
            label: 'Rework',
            condition: '',
            mode: 'human_review',
            position: { x: 0, y: 0 },
          },
          {
            id: 'rework',
            kind: 'stage',
            label: 'Rework it',
            runId: null,
            personaIds: [],
            position: { x: 0, y: 0 },
          },
        ]}
        edges={[
          {
            id: 'a',
            source: 'loop-gate',
            target: 'rework',
            cp1: { x: 0, y: 0 },
            cp2: { x: 0, y: 0 },
          },
          {
            id: 'b',
            source: 'rework',
            target: 'loop-gate',
            cp1: { x: 0, y: 0 },
            cp2: { x: 0, y: 0 },
          },
        ]}
      />,
    );
    expect(screen.getByTestId('workflow-strip-node-loop-gate')).toHaveTextContent('You approve');
  });
});
