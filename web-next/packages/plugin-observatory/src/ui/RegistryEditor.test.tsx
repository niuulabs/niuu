import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { RegistryEditor } from './RegistryEditor';
import type { Registry } from '../domain';

const SEED_REGISTRY: Registry = {
  version: 3,
  updatedAt: '2026-04-01T10:00:00Z',
  types: [
    {
      id: 'realm',
      label: 'Realm',
      rune: 'ᛞ',
      icon: 'globe',
      shape: 'ring',
      color: 'ice-100',
      size: 18,
      border: 'solid',
      canContain: ['cluster'],
      parentTypes: [],
      category: 'topology',
      description: 'VLAN-scoped network zone.',
      fields: [{ key: 'vlan', label: 'VLAN', type: 'number', required: true }],
    },
    {
      id: 'cluster',
      label: 'Cluster',
      rune: 'ᚲ',
      icon: 'layers',
      shape: 'ring-dashed',
      color: 'ice-200',
      size: 14,
      border: 'dashed',
      canContain: ['host'],
      parentTypes: ['realm'],
      category: 'topology',
      description: 'Kubernetes cluster nested inside a realm.',
      fields: [],
    },
    {
      id: 'host',
      label: 'Host',
      rune: 'ᚦ',
      icon: 'server',
      shape: 'rounded-rect',
      color: 'slate-400',
      size: 22,
      border: 'solid',
      canContain: [],
      parentTypes: ['cluster'],
      category: 'hardware',
      description: 'Bare-metal or VM.',
      fields: [{ key: 'os', label: 'OS', type: 'string' }],
    },
    {
      id: 'device',
      label: 'Device',
      rune: 'ᚠ',
      icon: 'wifi',
      shape: 'dot',
      color: 'slate-400',
      size: 5,
      border: 'dashed',
      canContain: [],
      parentTypes: [],
      category: 'device',
      description: 'A standalone device.',
      fields: [],
    },
  ],
};

// ── Render helpers ────────────────────────────────────────────────────────────

function renderEditor(registry = SEED_REGISTRY) {
  return render(<RegistryEditor registry={registry} />);
}

// ── Types tab ─────────────────────────────────────────────────────────────────

describe('RegistryEditor — Types tab', () => {
  it('renders the Types tab active by default', () => {
    renderEditor();
    const tab = screen.getByRole('tab', { name: /types/i });
    expect(tab).toHaveAttribute('aria-selected', 'true');
  });

  it('shows all entity types grouped by category', () => {
    renderEditor();
    // Use getAllByText since the first type may appear in both list and drawer
    expect(screen.getAllByText('Realm').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Cluster').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Host').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Device').length).toBeGreaterThan(0);
  });

  it('shows category headers', () => {
    renderEditor();
    // category headers appear in the list (uppercase mono label)
    expect(screen.getAllByText(/topology/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/hardware/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/device/).length).toBeGreaterThan(0);
  });

  it('shows the version and type count in the header', () => {
    renderEditor();
    expect(screen.getByText(/rev/)).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText(/4 types/)).toBeInTheDocument();
  });

  it('opens the preview drawer when a type row is clicked', () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('type-row-cluster'));
    expect(screen.getByTestId('type-preview-drawer')).toBeInTheDocument();
    // Drawer should show cluster details
    expect(screen.getAllByText('Cluster').length).toBeGreaterThan(0);
  });

  it('closes the preview drawer when the close button is clicked', () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('type-row-cluster'));
    fireEvent.click(screen.getByRole('button', { name: /close preview/i }));
    expect(screen.queryByTestId('type-preview-drawer')).not.toBeInTheDocument();
  });

  it('filters types by search query — hides non-matching type rows', () => {
    renderEditor();
    // Search for "VLAN" — only realm's description mentions VLAN
    fireEvent.change(screen.getByRole('textbox', { name: /filter types/i }), {
      target: { value: 'VLAN' },
    });
    expect(screen.getByTestId('type-row-realm')).toBeInTheDocument();
    expect(screen.queryByTestId('type-row-device')).not.toBeInTheDocument();
  });

  it('shows empty message when search matches nothing', () => {
    renderEditor();
    fireEvent.change(screen.getByRole('textbox', { name: /filter types/i }), {
      target: { value: 'zzznomatch' },
    });
    expect(screen.getByText(/no types match/i)).toBeInTheDocument();
  });
});

// ── Containment tab ───────────────────────────────────────────────────────────

describe('RegistryEditor — Containment tab', () => {
  it('switches to the Containment tab', () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('tab-containment'));
    expect(screen.getByTestId('containment-tree')).toBeInTheDocument();
  });

  it('shows root nodes (no parents) at top level', () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('tab-containment'));
    // realm and device are roots
    expect(screen.getByTestId('tree-node-realm')).toBeInTheDocument();
    expect(screen.getByTestId('tree-node-device')).toBeInTheDocument();
  });

  it('shows nested children of roots', () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('tab-containment'));
    expect(screen.getByTestId('tree-node-cluster')).toBeInTheDocument();
    expect(screen.getByTestId('tree-node-host')).toBeInTheDocument();
  });

  it('clicking a tree node opens the preview drawer', () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('tab-containment'));
    fireEvent.click(screen.getByTestId('tree-node-cluster'));
    expect(screen.getByTestId('type-preview-drawer')).toBeInTheDocument();
  });
});

// ── JSON tab ──────────────────────────────────────────────────────────────────

describe('RegistryEditor — JSON tab', () => {
  it('switches to the JSON tab', () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('tab-json'));
    expect(screen.getByTestId('json-output')).toBeInTheDocument();
  });

  it('renders pretty-printed JSON of the registry', () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('tab-json'));
    const pre = screen.getByTestId('json-output');
    expect(pre.textContent).toContain('"version": 3');
    expect(pre.textContent).toContain('"realm"');
  });

  it('renders the copy button', () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('tab-json'));
    expect(screen.getByTestId('copy-json-btn')).toBeInTheDocument();
    expect(screen.getByTestId('copy-json-btn')).toHaveTextContent('copy');
  });

  it('does not render the preview drawer in JSON tab', () => {
    renderEditor();
    // select a type first
    fireEvent.click(screen.getByTestId('type-row-realm'));
    // switch to JSON
    fireEvent.click(screen.getByTestId('tab-json'));
    expect(screen.queryByTestId('type-preview-drawer')).not.toBeInTheDocument();
  });
});

// ── Reparent (drag interaction) via tryReparent ────────────────────────────────

describe('RegistryEditor — containment reparenting', () => {
  it('updates the tree after a valid reparent (host → realm)', async () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('tab-containment'));

    // Simulate drag-drop by triggering dragStart + drop events
    const hostNode = screen.getByTestId('tree-node-host');
    const realmNode = screen.getByTestId('tree-node-realm');

    fireEvent.dragStart(hostNode);
    fireEvent.dragOver(realmNode);
    fireEvent.drop(realmNode);

    // After drop, the JSON tab should reflect the updated version
    fireEvent.click(screen.getByTestId('tab-json'));
    await waitFor(() => {
      const pre = screen.getByTestId('json-output');
      const parsed = JSON.parse(pre.textContent ?? '{}') as Registry;
      expect(parsed.version).toBe(4);
      const realm = parsed.types.find((t) => t.id === 'realm')!;
      expect(realm.canContain).toContain('host');
    });
  });

  it('marks node as drop-target state during valid dragOver', () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('tab-containment'));

    const hostNode = screen.getByTestId('tree-node-host');
    const realmNode = screen.getByTestId('tree-node-realm');

    fireEvent.dragStart(hostNode);
    fireEvent.dragOver(realmNode);

    expect(realmNode).toHaveAttribute('data-drag-state', 'target');
  });

  it('marks node as drop-invalid state when cycle would be created', () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('tab-containment'));

    // realm → cluster → host; dragging realm over host would cycle
    const realmNode = screen.getByTestId('tree-node-realm');
    const hostNode = screen.getByTestId('tree-node-host');

    fireEvent.dragStart(realmNode);
    fireEvent.dragOver(hostNode);

    expect(hostNode).toHaveAttribute('data-drag-state', 'invalid');
  });

  it('does not reparent when cycle would be created', () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('tab-containment'));

    const realmNode = screen.getByTestId('tree-node-realm');
    const hostNode = screen.getByTestId('tree-node-host');

    fireEvent.dragStart(realmNode);
    fireEvent.dragOver(hostNode);
    fireEvent.drop(hostNode);

    // Version should remain 3 — no change happened
    fireEvent.click(screen.getByTestId('tab-json'));
    const pre = screen.getByTestId('json-output');
    const parsed = JSON.parse(pre.textContent ?? '{}') as Registry;
    expect(parsed.version).toBe(3);
  });
});
