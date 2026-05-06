import type { Meta, StoryObj } from '@storybook/react';
import { Meter } from './Meter';

const meta: Meta<typeof Meter> = {
  title: 'Data Viz/Meter',
  component: Meter,
  args: { limit: 100, label: 'CPU' },
};
export default meta;

type Story = StoryObj<typeof Meter>;

export const Cool: Story = { args: { used: 30 } };

export const Warm: Story = { args: { used: 70 } };

export const Hot: Story = { args: { used: 92 } };

export const WithUnit: Story = { args: { used: 4, limit: 8, unit: 'c', label: 'Cores' } };

export const EmptyState: Story = { args: { used: null, limit: null } };

export const OverLimit: Story = { args: { used: 120 } };

export const AllLevels: Story = {
  render: () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, width: 240 }}>
      <Meter used={20} limit={100} label="Low" />
      <Meter used={50} limit={100} label="Mid" />
      <Meter used={70} limit={100} label="Warm" />
      <Meter used={90} limit={100} label="Hot" />
      <Meter used={null} limit={null} label="Empty" />
    </div>
  ),
};
