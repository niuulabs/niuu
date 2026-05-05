import type { Meta, StoryObj } from '@storybook/react';
import { ConfidenceBadge } from './ConfidenceBadge';

const meta: Meta<typeof ConfidenceBadge> = {
  title: 'Composites/ConfidenceBadge',
  component: ConfidenceBadge,
  args: { value: 0.85 },
};
export default meta;

type Story = StoryObj<typeof ConfidenceBadge>;

export const High: Story = { args: { value: 0.92 } };
export const Medium: Story = { args: { value: 0.64 } };
export const Low: Story = { args: { value: 0.28 } };
export const NullValue: Story = { args: { value: null } };
export const ZeroValue: Story = { args: { value: 0 } };
export const Boundary80: Story = { args: { value: 0.8 } };
export const Boundary50: Story = { args: { value: 0.5 } };
export const Boundary49: Story = { args: { value: 0.49 } };

export const AllVariants: Story = {
  render: () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-start' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <code style={{ fontSize: 11, width: 60 }}>null</code>
        <ConfidenceBadge value={null} />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <code style={{ fontSize: 11, width: 60 }}>0</code>
        <ConfidenceBadge value={0} />
      </div>
      {[0.28, 0.49, 0.5, 0.64, 0.8, 0.92, 1.0].map((v) => (
        <div key={v} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <code style={{ fontSize: 11, width: 60 }}>{v}</code>
          <ConfidenceBadge value={v} />
        </div>
      ))}
    </div>
  ),
};
