import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ObservatoryRailSections } from './ObservatoryRailSections';
import type { Topology, TopologyNode } from '../domain';

function node(id: string, typeId: string, over: Partial<TopologyNode> = {}): TopologyNode {
  return {
    id,
    typeId,
    label: id,
    parentId: null,
    status: 'healthy',
    ...over,
  } as TopologyNode;
}

function topology(nodes: TopologyNode[]): Topology {
  return { nodes, edges: [], timestamp: '2026-08-02T00:00:00Z' };
}

describe('ObservatoryRailSections', () => {
  it('lists residents with where they run', () => {
    render(
      <ObservatoryRailSections
        topology={topology([node('ivaldi', 'ravn_long', { cluster: 'eitri', engine: 'ravn' })])}
        selectedId={null}
        onSelect={vi.fn()}
      />,
    );

    // Engine first, then where it runs — what it is before where it is.
    expect(screen.getByTestId('rail-row-ivaldi')).toHaveTextContent('ravn · eitri');
  });

  it('marks a mesh without asking the camera to fly to one member', () => {
    // A mesh spans clusters on purpose, so travelling to whichever member
    // sorts first frames the least representative thing in it.
    const onSelect = vi.fn();
    render(
      <ObservatoryRailSections
        topology={topology([
          node('bryn', 'valkyrie', { flockId: 'flock-k8s', cluster: 'eitri' }),
          node('eir', 'valkyrie', { flockId: 'flock-k8s', cluster: 'noatun' }),
        ])}
        selectedId={null}
        onSelect={onSelect}
      />,
    );

    return userEvent.click(screen.getByTestId('rail-row-mesh-flock-k8s')).then(() => {
      expect(onSelect).toHaveBeenCalledWith('bryn', { focus: false });
    });
  });

  it('asks the camera to travel when a resident is picked', () => {
    const onSelect = vi.fn();
    render(
      <ObservatoryRailSections
        topology={topology([node('ivaldi', 'ravn_long', { cluster: 'eitri' })])}
        selectedId={null}
        onSelect={onSelect}
      />,
    );

    return userEvent.click(screen.getByTestId('rail-row-ivaldi')).then(() => {
      expect(onSelect).toHaveBeenCalledWith('ivaldi');
    });
  });

  it('states why a section is empty instead of rendering nothing', () => {
    // A deployment with no residents yet is not the same as a rail that lost
    // its section, and the operator has to be able to tell.
    render(
      <ObservatoryRailSections topology={topology([])} selectedId={null} onSelect={vi.fn()} />,
    );

    expect(screen.getByText('No residents reporting.')).toBeInTheDocument();
    expect(screen.getByText('No agent meshes discovered.')).toBeInTheDocument();
    expect(screen.getByText('No clusters discovered.')).toBeInTheDocument();
    expect(screen.getByText('No Mímir instances discovered.')).toBeInTheDocument();
  });

  it('selects a node when its row is clicked', async () => {
    const onSelect = vi.fn();
    render(
      <ObservatoryRailSections
        topology={topology([node('mimir-shared', 'mimir')])}
        selectedId={null}
        onSelect={onSelect}
      />,
    );

    await userEvent.click(screen.getByTestId('rail-row-mimir-shared'));

    expect(onSelect).toHaveBeenCalledWith('mimir-shared');
  });

  it('marks the selected row', () => {
    render(
      <ObservatoryRailSections
        topology={topology([node('ivaldi', 'ravn_long')])}
        selectedId="ivaldi"
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getByTestId('rail-row-ivaldi')).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows a mesh with its member count', () => {
    render(
      <ObservatoryRailSections
        topology={topology([
          node('a', 'ravn_long', { flockId: 'forge' }),
          node('b', 'ravn_long', { flockId: 'forge' }),
        ])}
        selectedId={null}
        onSelect={vi.fn()}
      />,
    );

    // Scoped to the mesh row: the Residents count is also 2.
    expect(screen.getByTestId('rail-row-mesh-forge')).toHaveTextContent('2');
  });

  it('survives an absent topology', () => {
    render(<ObservatoryRailSections topology={null} selectedId={null} onSelect={vi.fn()} />);

    expect(screen.getByText('No residents reporting.')).toBeInTheDocument();
  });
});
