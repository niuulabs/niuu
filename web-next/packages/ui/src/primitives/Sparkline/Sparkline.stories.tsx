import type { Meta, StoryObj } from '@storybook/react';
import { Sparkline } from './Sparkline';

const SAMPLE_24 = Array.from(
  { length: 24 },
  (_, i) => 0.3 + 0.5 * Math.sin(i / 4) + 0.1 * Math.cos(i / 2),
);

const meta: Meta<typeof Sparkline> = {
  title: 'Data Viz/Sparkline',
  component: Sparkline,
  args: { id: 'demo' },
};
export default meta;

type Story = StoryObj<typeof Sparkline>;

/** 24 seeded deterministic samples (no values prop — derived from id). */
export const Seeded24: Story = {
  args: { id: 'fleet-cost' },
};

/** Explicit 24-sample sine wave. */
export const Explicit24: Story = {
  args: { values: SAMPLE_24 },
};

/** Empty — renders a blank svg placeholder. */
export const Empty: Story = {
  args: { values: [] },
};

/** Single data point — renders a dot. */
export const SinglePoint: Story = {
  args: { values: [0.7] },
};

/** No area fill. */
export const LineOnly: Story = {
  args: { id: 'line-only', fill: false },
};

/** Larger canvas. */
export const Wide: Story = {
  args: { id: 'wide', width: 200, height: 40 },
};

/** Multiple sparklines side-by-side using different ids. */
export const DeterministicGrid: Story = {
  render: () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {['alpha', 'beta', 'gamma', 'delta'].map((id) => (
        <div key={id} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <code style={{ fontSize: 11, width: 48, color: 'var(--color-text-muted)' }}>{id}</code>
          <Sparkline id={id} />
        </div>
      ))}
    </div>
  ),
};
