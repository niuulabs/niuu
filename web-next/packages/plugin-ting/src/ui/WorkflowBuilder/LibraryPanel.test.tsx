import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { LibraryPanel, DEFAULT_PERSONAS } from './LibraryPanel';
import type { WorkflowRegistryMount } from './mimirRegistry';

const REGISTRY_MOUNT: WorkflowRegistryMount = {
  id: 'shared-mimir',
  name: 'Shared Mimir',
  kind: 'remote',
  lifecycle: 'registered',
  role: 'shared',
  url: 'https://mimir.example',
  path: '/shared',
  categories: ['decision', 'entity'],
  authRef: 'mimir-secret',
  defaultReadPriority: 5,
  enabled: true,
  healthStatus: 'healthy',
  healthMessage: 'ok',
  desc: 'Shared team mount',
};

describe('LibraryPanel', () => {
  it('renders the library-panel container', () => {
    render(<LibraryPanel personas={DEFAULT_PERSONAS} />);
    expect(screen.getByTestId('library-panel')).toBeInTheDocument();
    expect(screen.getByText('External wait')).toBeInTheDocument();
  });

  it('renders a chip for each persona', () => {
    render(<LibraryPanel personas={DEFAULT_PERSONAS} />);
    for (const persona of DEFAULT_PERSONAS) {
      expect(screen.getByTestId(`persona-chip-${persona.id}`)).toBeInTheDocument();
    }
  });

  it('displays persona labels', () => {
    render(<LibraryPanel personas={DEFAULT_PERSONAS} />);
    expect(screen.getByText('coder')).toBeInTheDocument();
    expect(screen.getByText('mimir-memory-curator')).toBeInTheDocument();
  });

  it('renders with custom personas', () => {
    const custom = [{ id: 'custom-1', label: 'Custom', role: 'custom' }];
    render(<LibraryPanel personas={custom} />);
    expect(screen.getByTestId('persona-chip-custom-1')).toBeInTheDocument();
    expect(screen.getByText('Custom')).toBeInTheDocument();
  });

  it('renders with empty personas list', () => {
    render(<LibraryPanel personas={[]} />);
    expect(screen.getByTestId('library-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('persona-chip-persona-plan')).toBeNull();
  });

  it('chips are draggable', () => {
    render(<LibraryPanel personas={DEFAULT_PERSONAS} />);
    const chip = screen.getByTestId(`persona-chip-${DEFAULT_PERSONAS[0]!.id}`);
    expect(chip).toHaveAttribute('draggable', 'true');
  });

  it('renders registry-backed Mimir mounts when provided', () => {
    render(<LibraryPanel personas={DEFAULT_PERSONAS} registryMounts={[REGISTRY_MOUNT]} />);
    expect(screen.getByTestId('mimir-mount-shared-mimir')).toBeInTheDocument();
    expect(screen.getByText('Shared Mimir')).toBeInTheDocument();
  });

  it('always renders the explicit ephemeral local Mimir resource', () => {
    render(<LibraryPanel personas={DEFAULT_PERSONAS} />);
    expect(screen.getByText('Ephemeral Local Mimir')).toBeInTheDocument();
    expect(screen.getByText(/workspace-local scratch/i)).toBeInTheDocument();
  });

  it('adds flow control and personas directly from the contextual picker', () => {
    const onAddNode = vi.fn();
    const onAddPersona = vi.fn();
    const onClose = vi.fn();
    render(
      <LibraryPanel
        personas={DEFAULT_PERSONAS}
        onAddNode={onAddNode}
        onAddPersona={onAddPersona}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByTestId('library-add-wait'));
    expect(onAddNode).toHaveBeenCalledWith('wait');
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId(`persona-chip-${DEFAULT_PERSONAS[0]!.id}`));
    expect(onAddPersona).toHaveBeenCalledWith(DEFAULT_PERSONAS[0]!.id);
  });

  it('searches flow-control candidates as well as actors and resources', () => {
    render(<LibraryPanel personas={DEFAULT_PERSONAS} />);
    fireEvent.change(screen.getByTestId('library-search'), { target: { value: 'child' } });
    expect(screen.getByTestId('library-add-subworkflow')).toBeInTheDocument();
    expect(screen.queryByTestId('library-add-stage')).not.toBeInTheDocument();
  });

  it('DEFAULT_PERSONAS has 5 entries', () => {
    expect(DEFAULT_PERSONAS).toHaveLength(5);
  });

  it('DEFAULT_PERSONAS all have unique ids', () => {
    const ids = DEFAULT_PERSONAS.map((p) => p.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('sets drag payloads for flow-control blocks, mounts and personas', () => {
    render(<LibraryPanel personas={DEFAULT_PERSONAS} registryMounts={[REGISTRY_MOUNT]} />);
    const setData = vi.fn();
    const dataTransfer = { setData, effectAllowed: '' };

    fireEvent.dragStart(screen.getByTestId('library-add-wait'), { dataTransfer });
    expect(setData).toHaveBeenCalledWith('application/niuu-node-kind', 'wait');

    fireEvent.dragStart(screen.getByTestId('mimir-mount-shared-mimir'), { dataTransfer });
    expect(setData).toHaveBeenCalledWith(
      'application/niuu-mimir-mount',
      expect.stringContaining('shared-mimir'),
    );

    fireEvent.dragStart(screen.getByTestId(`persona-chip-${DEFAULT_PERSONAS[0]!.id}`), {
      dataTransfer,
    });
    expect(setData).toHaveBeenCalledWith('application/niuu-persona-id', DEFAULT_PERSONAS[0]!.id);
  });

  it('renders the triangle glyph for a verify-role persona', () => {
    render(<LibraryPanel personas={[{ id: 'verifier-1', label: 'Verifier', role: 'verify' }]} />);
    expect(screen.getByText('V')).toBeInTheDocument();
    expect(screen.getByText('△')).toBeInTheDocument();
  });

  it('renders the dashed-circle glyph for a plan-role persona', () => {
    render(<LibraryPanel personas={[{ id: 'planner-1', label: 'Planner', role: 'plan' }]} />);
    expect(screen.getByText('D')).toBeInTheDocument();
  });

  it('renders the hex glyph for a gate-role persona', () => {
    render(<LibraryPanel personas={[{ id: 'gatekeeper-1', label: 'Gatekeeper', role: 'gate' }]} />);
    expect(screen.getByText('I')).toBeInTheDocument();
  });

  it('adds a registered Mimir resource and closes when a handler is set', () => {
    const onAddMimirResource = vi.fn();
    const onClose = vi.fn();
    render(
      <LibraryPanel
        personas={DEFAULT_PERSONAS}
        registryMounts={[REGISTRY_MOUNT]}
        onAddMimirResource={onAddMimirResource}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByTestId('mimir-mount-shared-mimir'));
    expect(onAddMimirResource).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'shared-mimir' }),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes the panel from the close control and on Escape in search', () => {
    const onClose = vi.fn();
    render(<LibraryPanel personas={DEFAULT_PERSONAS} onClose={onClose} />);
    fireEvent.keyDown(screen.getByTestId('library-search'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId('library-panel-close'));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('does not render a close control when no handler is given', () => {
    render(<LibraryPanel personas={DEFAULT_PERSONAS} />);
    expect(screen.queryByTestId('library-panel-close')).not.toBeInTheDocument();
  });

  it('hides the resource section entirely when embedded without a Mimir handler', () => {
    render(<LibraryPanel embedded personas={DEFAULT_PERSONAS} registryMounts={[REGISTRY_MOUNT]} />);
    expect(screen.queryByText('Ephemeral Local Mimir')).not.toBeInTheDocument();
    expect(screen.queryByTestId('mimir-mount-shared-mimir')).not.toBeInTheDocument();
  });

  it('shows resources when embedded with a Mimir handler wired up', () => {
    render(
      <LibraryPanel
        embedded
        personas={DEFAULT_PERSONAS}
        registryMounts={[REGISTRY_MOUNT]}
        onAddMimirResource={vi.fn()}
      />,
    );
    expect(screen.getByText('Ephemeral Local Mimir')).toBeInTheDocument();
  });

  it('filters resources by category and hides flow control when nothing matches', () => {
    render(<LibraryPanel personas={DEFAULT_PERSONAS} registryMounts={[REGISTRY_MOUNT]} />);
    fireEvent.change(screen.getByTestId('library-search'), { target: { value: 'decision' } });
    expect(screen.getByTestId('mimir-mount-shared-mimir')).toBeInTheDocument();
    expect(screen.queryByTestId('library-add-wait')).not.toBeInTheDocument();
  });
});
