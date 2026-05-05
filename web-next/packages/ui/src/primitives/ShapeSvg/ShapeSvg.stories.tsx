import type { Meta, StoryObj } from '@storybook/react';
import { ShapeSvg, type ShapeKind, type ShapeColor } from './ShapeSvg';
import { ENTITY_RUNES, SERVICE_RUNES } from './runeMap';

const ALL_SHAPES: ShapeKind[] = [
  'ring',
  'ring-dashed',
  'rounded-rect',
  'diamond',
  'triangle',
  'hex',
  'chevron',
  'square',
  'square-sm',
  'pentagon',
  'halo',
  'mimir',
  'mimir-small',
  'dot',
];

const ALL_COLORS: ShapeColor[] = [
  'brand',
  'brand-100',
  'brand-200',
  'brand-300',
  'brand-400',
  'brand-500',
  'ice-100',
  'ice-200',
  'ice-300',
  'slate-300',
  'slate-400',
];

const meta: Meta<typeof ShapeSvg> = {
  title: 'Primitives/ShapeSvg',
  component: ShapeSvg,
  args: { shape: 'ring', size: 20 },
};
export default meta;

type Story = StoryObj<typeof ShapeSvg>;

export const Default: Story = {};

export const AllShapes20: Story = {
  name: 'All Shapes — 20×20',
  render: () => (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'auto 1fr',
        gap: '8px 16px',
        alignItems: 'center',
      }}
    >
      {ALL_SHAPES.map((s) => (
        <div key={s} style={{ display: 'contents' }}>
          <ShapeSvg shape={s} size={20} />
          <code style={{ fontSize: 12 }}>{s}</code>
        </div>
      ))}
    </div>
  ),
};

export const AllShapes36: Story = {
  name: 'All Shapes — 36×36',
  render: () => (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'auto 1fr',
        gap: '12px 20px',
        alignItems: 'center',
      }}
    >
      {ALL_SHAPES.map((s) => (
        <div key={s} style={{ display: 'contents' }}>
          <ShapeSvg shape={s} size={36} />
          <code style={{ fontSize: 12 }}>{s}</code>
        </div>
      ))}
    </div>
  ),
};

export const ColorPalette: Story = {
  name: 'Color Palette',
  render: () => (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'auto 1fr',
        gap: '8px 16px',
        alignItems: 'center',
      }}
    >
      {ALL_COLORS.map((c) => (
        <div key={c} style={{ display: 'contents' }}>
          <ShapeSvg shape="diamond" size={24} color={c} />
          <code style={{ fontSize: 12 }}>{c}</code>
        </div>
      ))}
    </div>
  ),
};

export const EntityRuneTable: Story = {
  name: 'Entity Rune Table',
  render: () => (
    <div style={{ fontFamily: 'var(--font-sans, system-ui)', fontSize: 13 }}>
      <p style={{ marginBottom: 16, color: 'var(--color-text-secondary, #a1a1aa)' }}>
        Entity-kind glyphs keyed by registry type ID
      </p>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'auto auto 1fr',
          gap: '6px 20px',
          alignItems: 'center',
        }}
      >
        <strong>Kind</strong>
        <strong>Rune</strong>
        <strong />
        {Object.entries(ENTITY_RUNES).map(([key, rune]) => (
          <div key={key} style={{ display: 'contents' }}>
            <code style={{ fontSize: 12 }}>{key}</code>
            <span style={{ fontSize: 22, fontFamily: 'var(--font-mono, monospace)' }}>{rune}</span>
            <span />
          </div>
        ))}
      </div>
    </div>
  ),
};

export const ServiceRuneTable: Story = {
  name: 'Service Rune Table',
  render: () => (
    <div style={{ fontFamily: 'var(--font-sans, system-ui)', fontSize: 13 }}>
      <p style={{ marginBottom: 16, color: 'var(--color-text-secondary, #a1a1aa)' }}>
        Brand-identity glyphs keyed by system name (DS_RUNES)
      </p>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'auto auto 1fr',
          gap: '6px 20px',
          alignItems: 'center',
        }}
      >
        <strong>Service</strong>
        <strong>Rune</strong>
        <strong />
        {Object.entries(SERVICE_RUNES).map(([key, rune]) => (
          <div key={key} style={{ display: 'contents' }}>
            <code style={{ fontSize: 12 }}>{key}</code>
            <span style={{ fontSize: 22, fontFamily: 'var(--font-mono, monospace)' }}>{rune}</span>
            <span />
          </div>
        ))}
      </div>
    </div>
  ),
};
