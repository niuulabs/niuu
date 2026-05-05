import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { NodeInspector } from './NodeInspector';
import type { WorkflowNode } from '../../domain/workflow';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const stageNode: WorkflowNode = {
  id: 'stage-1',
  kind: 'stage',
  label: 'Stage 1',
  raidId: 'raid-abc',
  personaIds: ['persona-build', 'persona-plan'],
  position: { x: 100, y: 100 },
};

const gateNode: WorkflowNode = {
  id: 'gate-1',
  kind: 'gate',
  label: 'Gate',
  condition: 'all tests pass',
  position: { x: 300, y: 100 },
};

const condNode: WorkflowNode = {
  id: 'cond-1',
  kind: 'cond',
  label: 'Cond',
  predicate: 'ci.exitCode === 0',
  position: { x: 500, y: 100 },
};

function defaultHandlers() {
  return {
    onClose: vi.fn(),
    onUpdateLabel: vi.fn(),
    onAddPersona: vi.fn(),
    onRemovePersona: vi.fn(),
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('NodeInspector — stage node', () => {
  it('renders the node-inspector container', () => {
    render(<NodeInspector node={stageNode} {...defaultHandlers()} />);
    expect(screen.getByTestId('node-inspector')).toBeInTheDocument();
  });

  it('renders the label input with current value', () => {
    render(<NodeInspector node={stageNode} {...defaultHandlers()} />);
    const input = screen.getByTestId('inspector-label') as HTMLInputElement;
    expect(input.value).toBe('Stage 1');
  });

  it('calls onUpdateLabel with new value on blur', () => {
    const handlers = defaultHandlers();
    render(<NodeInspector node={stageNode} {...handlers} />);
    const input = screen.getByTestId('inspector-label');
    fireEvent.change(input, { target: { value: 'Updated Label' } });
    fireEvent.blur(input);
    expect(handlers.onUpdateLabel).toHaveBeenCalledWith('stage-1', 'Updated Label');
  });

  it('shows the raid ID', () => {
    render(<NodeInspector node={stageNode} {...defaultHandlers()} />);
    expect(screen.getByTestId('inspector-raid-id')).toHaveTextContent('raid-abc');
  });

  it('shows "unassigned" when raidId is null', () => {
    const node = { ...stageNode, raidId: null };
    render(<NodeInspector node={node} {...defaultHandlers()} />);
    expect(screen.getByTestId('inspector-raid-id')).toHaveTextContent('unassigned');
  });

  it('renders persona chips for assigned personas', () => {
    render(<NodeInspector node={stageNode} {...defaultHandlers()} />);
    expect(screen.getByTestId('inspector-persona-persona-build')).toBeInTheDocument();
    expect(screen.getByTestId('inspector-persona-persona-plan')).toBeInTheDocument();
  });

  it('calls onRemovePersona when remove button clicked', () => {
    const handlers = defaultHandlers();
    render(<NodeInspector node={stageNode} {...handlers} />);
    fireEvent.click(screen.getByTestId('remove-persona-persona-build'));
    expect(handlers.onRemovePersona).toHaveBeenCalledWith('stage-1', 'persona-build');
  });

  it('shows "None assigned" when personaIds is empty', () => {
    const node = { ...stageNode, personaIds: [] };
    render(<NodeInspector node={node} {...defaultHandlers()} />);
    expect(screen.getByText('None assigned')).toBeInTheDocument();
  });

  it('does not show condition field for stage node', () => {
    render(<NodeInspector node={stageNode} {...defaultHandlers()} />);
    expect(screen.queryByTestId('inspector-condition')).toBeNull();
  });

  it('does not show predicate field for stage node', () => {
    render(<NodeInspector node={stageNode} {...defaultHandlers()} />);
    expect(screen.queryByTestId('inspector-predicate')).toBeNull();
  });
});

describe('NodeInspector — gate node', () => {
  it('renders inspector for gate node', () => {
    render(<NodeInspector node={gateNode} {...defaultHandlers()} />);
    expect(screen.getByTestId('node-inspector')).toBeInTheDocument();
  });

  it('shows the gate condition', () => {
    render(<NodeInspector node={gateNode} {...defaultHandlers()} />);
    expect(screen.getByTestId('inspector-condition')).toHaveTextContent('all tests pass');
  });

  it('does not show persona section for gate node', () => {
    render(<NodeInspector node={gateNode} {...defaultHandlers()} />);
    expect(screen.queryByTestId('inspector-raid-id')).toBeNull();
  });
});

describe('NodeInspector — cond node', () => {
  it('renders inspector for cond node', () => {
    render(<NodeInspector node={condNode} {...defaultHandlers()} />);
    expect(screen.getByTestId('node-inspector')).toBeInTheDocument();
  });

  it('shows the cond predicate', () => {
    render(<NodeInspector node={condNode} {...defaultHandlers()} />);
    expect(screen.getByTestId('inspector-predicate')).toHaveTextContent('ci.exitCode === 0');
  });

  it('does not show condition field for cond node', () => {
    render(<NodeInspector node={condNode} {...defaultHandlers()} />);
    expect(screen.queryByTestId('inspector-condition')).toBeNull();
  });
});
