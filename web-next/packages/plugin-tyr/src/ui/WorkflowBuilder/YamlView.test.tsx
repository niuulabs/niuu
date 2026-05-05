import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { YamlView } from './YamlView';
import type { Workflow } from '../../domain/workflow';

const workflow: Workflow = {
  id: '00000000-0000-0000-0000-000000000001',
  name: 'Test Workflow',
  nodes: [
    {
      id: 'stage-1',
      kind: 'stage',
      label: 'Set up CI',
      raidId: 'raid-123',
      personaIds: ['persona-build'],
      position: { x: 100, y: 100 },
    },
    {
      id: 'gate-1',
      kind: 'gate',
      label: 'QA sign-off',
      condition: 'all tests pass',
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

describe('YamlView', () => {
  it('renders the yaml-view container', () => {
    render(<YamlView workflow={workflow} />);
    expect(screen.getByTestId('yaml-view')).toBeInTheDocument();
  });

  it('renders the yaml-content pre element', () => {
    render(<YamlView workflow={workflow} />);
    expect(screen.getByTestId('yaml-content')).toBeInTheDocument();
  });

  it('displays the workflow id', () => {
    render(<YamlView workflow={workflow} />);
    expect(screen.getByTestId('yaml-content').textContent).toContain(workflow.id);
  });

  it('displays the workflow name', () => {
    render(<YamlView workflow={workflow} />);
    expect(screen.getByTestId('yaml-content').textContent).toContain('Test Workflow');
  });

  it('displays stage-1 node id', () => {
    render(<YamlView workflow={workflow} />);
    expect(screen.getByTestId('yaml-content').textContent).toContain('stage-1');
  });

  it('displays gate-1 node id', () => {
    render(<YamlView workflow={workflow} />);
    expect(screen.getByTestId('yaml-content').textContent).toContain('gate-1');
  });

  it('displays edge source and target', () => {
    render(<YamlView workflow={workflow} />);
    const text = screen.getByTestId('yaml-content').textContent ?? '';
    expect(text).toContain('source:');
    expect(text).toContain('target:');
  });

  it('displays personaIds content', () => {
    render(<YamlView workflow={workflow} />);
    expect(screen.getByTestId('yaml-content').textContent).toContain('persona-build');
  });

  it('displays empty nodes for workflow with no nodes', () => {
    const empty: Workflow = { ...workflow, nodes: [], edges: [] };
    render(<YamlView workflow={empty} />);
    expect(screen.getByTestId('yaml-content').textContent).toContain('nodes: []');
    expect(screen.getByTestId('yaml-content').textContent).toContain('edges: []');
  });
});
