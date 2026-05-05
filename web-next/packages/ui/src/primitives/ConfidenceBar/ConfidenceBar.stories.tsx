import type { Meta, StoryObj } from '@storybook/react';
import { ConfidenceBar } from './ConfidenceBar';

const meta: Meta<typeof ConfidenceBar> = {
  title: 'Composites/ConfidenceBar',
  component: ConfidenceBar,
  args: { level: 'high' },
};
export default meta;

type Story = StoryObj<typeof ConfidenceBar>;

export const High: Story = { args: { level: 'high' } };
export const Medium: Story = { args: { level: 'medium' } };
export const Low: Story = { args: { level: 'low' } };

export const AllLevels: Story = {
  render: () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-start' }}>
      <ConfidenceBar level="high" />
      <ConfidenceBar level="medium" />
      <ConfidenceBar level="low" />
    </div>
  ),
};
