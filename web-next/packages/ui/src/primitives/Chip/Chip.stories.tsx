import type { Meta, StoryObj } from '@storybook/react';
import { Chip } from './Chip';

const meta: Meta<typeof Chip> = {
  title: 'Primitives/Chip',
  component: Chip,
  args: { children: 'hello' },
};
export default meta;

type Story = StoryObj<typeof Chip>;

export const Default: Story = {};
export const Brand: Story = { args: { tone: 'brand', children: 'ice' } };
export const Critical: Story = { args: { tone: 'critical', children: 'failed' } };
export const Muted: Story = { args: { tone: 'muted', children: 'idle' } };

export const AllTones: Story = {
  render: () => (
    <div style={{ display: 'flex', gap: 12 }}>
      <Chip>default</Chip>
      <Chip tone="brand">brand</Chip>
      <Chip tone="critical">critical</Chip>
      <Chip tone="muted">muted</Chip>
    </div>
  ),
};
