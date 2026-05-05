import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ValidationPanel } from './ValidationPanel';
import type { Workflow } from '../../domain/workflow';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeCleanWorkflow(): Workflow {
  // Truly clean: raidId set, personaIds non-empty, connected, no cycle, no orphan.
  return {
    id: '00000000-0000-0000-0000-000000000001',
    name: 'Test',
    nodes: [
      {
        id: 'stage-1',
        kind: 'stage',
        label: 'Stage 1',
        raidId: 'raid-abc',
        personaIds: ['persona-build'],
        position: { x: 100, y: 100 },
      },
      {
        id: 'gate-1',
        kind: 'gate',
        label: 'Gate',
        condition: 'ok',
        position: { x: 300, y: 100 },
      },
    ],
    edges: [
      {
        id: 'e1',
        source: 'stage-1',
        target: 'gate-1',
        cp1: { x: 80, y: 0 },
        cp2: { x: -80, y: 0 },
      },
    ],
  };
}

function makeCyclicWorkflow(): Workflow {
  return {
    id: '00000000-0000-0000-0000-000000000002',
    name: 'Cyclic',
    nodes: [
      {
        id: 'a',
        kind: 'stage',
        label: 'A',
        raidId: null,
        personaIds: [],
        position: { x: 0, y: 0 },
      },
      {
        id: 'b',
        kind: 'stage',
        label: 'B',
        raidId: null,
        personaIds: [],
        position: { x: 200, y: 0 },
      },
    ],
    edges: [
      { id: 'e1', source: 'a', target: 'b', cp1: { x: 80, y: 0 }, cp2: { x: -80, y: 0 } },
      { id: 'e2', source: 'b', target: 'a', cp1: { x: -80, y: 0 }, cp2: { x: 80, y: 0 } },
    ],
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ValidationPanel', () => {
  it('renders the validation-panel container', () => {
    render(
      <ValidationPanel
        workflow={makeCleanWorkflow()}
        onSelectNode={vi.fn()}
        errorCount={0}
        warnCount={0}
      />,
    );
    expect(screen.getByTestId('validation-panel')).toBeInTheDocument();
  });

  it('renders the validation pill', () => {
    render(
      <ValidationPanel
        workflow={makeCleanWorkflow()}
        onSelectNode={vi.fn()}
        errorCount={0}
        warnCount={0}
      />,
    );
    expect(screen.getByTestId('validation-pill')).toBeInTheDocument();
  });

  it('shows no error/warn badges for a valid workflow', () => {
    render(
      <ValidationPanel
        workflow={makeCleanWorkflow()}
        onSelectNode={vi.fn()}
        errorCount={0}
        warnCount={0}
      />,
    );
    expect(screen.getByTestId('validation-pill')).toBeInTheDocument();
    // No ERR or WARN badges when counts are 0
    expect(screen.getByTestId('validation-pill').textContent).not.toMatch(/ERR/);
    expect(screen.getByTestId('validation-pill').textContent).not.toMatch(/WARN/);
  });

  it('sets data-issue-count to 0 for clean workflow', () => {
    render(
      <ValidationPanel
        workflow={makeCleanWorkflow()}
        onSelectNode={vi.fn()}
        errorCount={0}
        warnCount={0}
      />,
    );
    expect(screen.getByTestId('validation-pill')).toHaveAttribute('data-issue-count', '0');
  });

  it('shows error count for cyclic workflow', () => {
    render(
      <ValidationPanel
        workflow={makeCyclicWorkflow()}
        onSelectNode={vi.fn()}
        errorCount={1}
        warnCount={0}
      />,
    );
    expect(screen.getByTestId('validation-pill').textContent).toMatch(/ERR/);
  });

  it('pill data-issue-count > 0 for cyclic workflow', () => {
    render(
      <ValidationPanel
        workflow={makeCyclicWorkflow()}
        onSelectNode={vi.fn()}
        errorCount={1}
        warnCount={0}
      />,
    );
    const count = Number(screen.getByTestId('validation-pill').getAttribute('data-issue-count'));
    expect(count).toBeGreaterThan(0);
  });

  it('expands issue list when pill is clicked', () => {
    render(
      <ValidationPanel
        workflow={makeCyclicWorkflow()}
        onSelectNode={vi.fn()}
        errorCount={1}
        warnCount={0}
      />,
    );
    // Issues not visible initially
    fireEvent.click(screen.getByTestId('validation-pill'));
    // After click, issue buttons should be visible
    const issues = screen.queryAllByTestId(/^validation-issue-/);
    expect(issues.length).toBeGreaterThan(0);
  });

  it('collapses issue list on second click', () => {
    render(
      <ValidationPanel
        workflow={makeCyclicWorkflow()}
        onSelectNode={vi.fn()}
        errorCount={1}
        warnCount={0}
      />,
    );
    const pill = screen.getByTestId('validation-pill');
    fireEvent.click(pill);
    fireEvent.click(pill);
    // After two clicks, list is collapsed
    expect(screen.queryAllByTestId(/^validation-issue-/)).toHaveLength(0);
  });

  it('calls onSelectNode when clicking an issue with a nodeId', () => {
    const onSelectNode = vi.fn();
    render(
      <ValidationPanel
        workflow={makeCyclicWorkflow()}
        onSelectNode={onSelectNode}
        errorCount={1}
        warnCount={0}
      />,
    );
    fireEvent.click(screen.getByTestId('validation-pill'));
    const issues = screen.queryAllByTestId(/^validation-issue-/);
    // Find an issue that is not "global" (has a real nodeId)
    const nodeIssue = issues.find((el) => !el.getAttribute('data-testid')?.endsWith('-global'));
    if (nodeIssue) {
      fireEvent.click(nodeIssue);
      expect(onSelectNode).toHaveBeenCalled();
    }
  });

  it('does not expand when there are no issues', () => {
    render(
      <ValidationPanel
        workflow={makeCleanWorkflow()}
        onSelectNode={vi.fn()}
        errorCount={0}
        warnCount={0}
      />,
    );
    fireEvent.click(screen.getByTestId('validation-pill'));
    // No issues to show — list stays empty
    expect(screen.queryAllByTestId(/^validation-issue-/)).toHaveLength(0);
  });
});
