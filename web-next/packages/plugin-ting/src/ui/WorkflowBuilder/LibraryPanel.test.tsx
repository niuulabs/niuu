import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
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

  it('DEFAULT_PERSONAS has 5 entries', () => {
    expect(DEFAULT_PERSONAS).toHaveLength(5);
  });

  it('DEFAULT_PERSONAS all have unique ids', () => {
    const ids = DEFAULT_PERSONAS.map((p) => p.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
