import type { Meta, StoryObj } from '@storybook/react';
import { RegistryEditor } from './RegistryEditor';
import type { Registry } from '../domain';

/**
 * Registry editor — three-tab interface for browsing and reparenting entity
 * types in the Observatory.
 *
 * - **Types**: searchable list grouped by category, click to open preview drawer.
 * - **Containment**: drag-drop tree for reparenting; cycle protection enforced.
 * - **JSON**: read-only pretty-print with copy button.
 */
const meta: Meta<typeof RegistryEditor> = {
  title: 'Plugins / Observatory / RegistryEditor',
  component: RegistryEditor,
  parameters: {
    layout: 'fullscreen',
  },
  decorators: [
    (Story) => (
      <div style={{ height: '100vh', background: 'var(--color-bg-primary)' }}>
        <Story />
      </div>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof RegistryEditor>;

/** Default state — full seed registry, Types tab, first type pre-selected. */
export const Default: Story = {
  args: { registry: FULL_REGISTRY },
};

/** Minimal registry — 3 types — useful for verifying layout at small scale. */
export const Minimal: Story = {
  args: { registry: MINIMAL_REGISTRY },
};

/** Registry containing an orphaned type (parentType references a missing id). */
export const WithOrphan: Story = {
  args: { registry: ORPHAN_REGISTRY },
};

// ── Seed data ─────────────────────────────────────────────────────────────────

const MINIMAL_REGISTRY: Registry = {
  version: 1,
  updatedAt: '2026-04-01T00:00:00Z',
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
      fields: [],
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
      description: 'Kubernetes cluster.',
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
  ],
};

const ORPHAN_REGISTRY: Registry = {
  ...MINIMAL_REGISTRY,
  types: [
    ...MINIMAL_REGISTRY.types,
    {
      id: 'orphan',
      label: 'Orphaned Type',
      rune: 'ᚲ',
      icon: 'box',
      shape: 'dot',
      color: 'slate-400',
      size: 8,
      border: 'solid',
      canContain: [],
      parentTypes: ['missing-parent'],
      category: 'infrastructure',
      description: 'This type references a parent that no longer exists.',
      fields: [],
    },
  ],
};

const FULL_REGISTRY: Registry = {
  version: 7,
  updatedAt: '2026-04-15T09:24:11Z',
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
      canContain: ['cluster', 'host', 'ravn_long', 'valkyrie', 'printer', 'vaettir', 'beacon'],
      parentTypes: [],
      category: 'topology',
      description:
        'VLAN-scoped network zone — asgard, midgard, svartalfheim, etc. Every entity lives in exactly one realm.',
      fields: [
        { key: 'vlan', label: 'VLAN', type: 'number', required: true },
        { key: 'dns', label: 'DNS zone', type: 'string', required: true },
        { key: 'purpose', label: 'Purpose', type: 'string' },
      ],
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
      canContain: ['service', 'raid', 'tyr', 'bifrost', 'volundr', 'valkyrie', 'mimir'],
      parentTypes: ['realm'],
      category: 'topology',
      description:
        'Kubernetes cluster nested inside a realm. Valaskjálf, Valhalla, Nóatún, Eitri, Glitnir, Járnviðr.',
      fields: [
        { key: 'purpose', label: 'Purpose', type: 'string' },
        { key: 'nodes', label: 'Nodes', type: 'number' },
      ],
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
      canContain: ['ravn_long', 'service'],
      parentTypes: ['realm'],
      category: 'hardware',
      description: 'Bare-metal or VM. DGX Sparks, Mac minis, EPYC boxes, user laptops.',
      fields: [
        { key: 'hw', label: 'Hardware', type: 'string' },
        { key: 'os', label: 'OS', type: 'string' },
        { key: 'cores', label: 'Cores', type: 'number' },
        { key: 'ram', label: 'RAM', type: 'string' },
        { key: 'gpu', label: 'GPU', type: 'string' },
      ],
    },
    {
      id: 'ravn_long',
      label: 'Long-lived Ravn',
      rune: 'ᚱ',
      icon: 'bird',
      shape: 'diamond',
      color: 'brand',
      size: 11,
      border: 'solid',
      canContain: [],
      parentTypes: ['host', 'cluster', 'realm'],
      category: 'agent',
      description: 'Persistent raven agent bound to a host or free-orbiting.',
      fields: [
        { key: 'persona', label: 'Persona', type: 'select', options: ['thought', 'memory'] },
        { key: 'specialty', label: 'Specialty', type: 'string' },
      ],
    },
    {
      id: 'tyr',
      label: 'Týr',
      rune: 'ᛃ',
      icon: 'git-branch',
      shape: 'square',
      color: 'brand',
      size: 16,
      border: 'solid',
      canContain: [],
      parentTypes: ['cluster', 'realm'],
      category: 'coordinator',
      description: 'Saga / raid orchestrator.',
      fields: [{ key: 'activeSagas', label: 'Active sagas', type: 'number' }],
    },
    {
      id: 'mimir',
      label: 'Mímir',
      rune: 'ᛗ',
      icon: 'book-open',
      shape: 'mimir',
      color: 'ice-100',
      size: 42,
      border: 'solid',
      canContain: ['mimir_sub'],
      parentTypes: ['cluster', 'realm'],
      category: 'knowledge',
      description: 'The well of knowledge.',
      fields: [{ key: 'pages', label: 'Pages', type: 'number' }],
    },
    {
      id: 'mimir_sub',
      label: 'Sub-Mímir',
      rune: 'ᛗ',
      icon: 'book-marked',
      shape: 'mimir-small',
      color: 'ice-200',
      size: 18,
      border: 'solid',
      canContain: [],
      parentTypes: ['mimir'],
      category: 'knowledge',
      description: 'Domain-scoped Mímir.',
      fields: [{ key: 'purpose', label: 'Purpose', type: 'string' }],
    },
    {
      id: 'service',
      label: 'Service',
      rune: 'ᛦ',
      icon: 'box',
      shape: 'dot',
      color: 'ice-300',
      size: 8,
      border: 'solid',
      canContain: [],
      parentTypes: ['cluster', 'host'],
      category: 'infrastructure',
      description: 'Kubernetes workload.',
      fields: [{ key: 'svcType', label: 'Type', type: 'select', options: ['rabbitmq', 'auth'] }],
    },
    {
      id: 'raid',
      label: 'Raid',
      rune: 'ᚷ',
      icon: 'users',
      shape: 'halo',
      color: 'brand',
      size: 50,
      border: 'dashed',
      canContain: ['ravn_raid'],
      parentTypes: ['cluster'],
      category: 'composite',
      description: 'Ephemeral flock of ravens.',
      fields: [{ key: 'purpose', label: 'Purpose', type: 'string' }],
    },
    {
      id: 'ravn_raid',
      label: 'Raid Ravn',
      rune: 'ᚲ',
      icon: 'bird',
      shape: 'triangle',
      color: 'ice-300',
      size: 8,
      border: 'solid',
      canContain: [],
      parentTypes: ['raid'],
      category: 'agent',
      description: 'Ephemeral raven in a raid.',
      fields: [{ key: 'role', label: 'Role', type: 'select', options: ['coord', 'reviewer'] }],
    },
  ],
};
