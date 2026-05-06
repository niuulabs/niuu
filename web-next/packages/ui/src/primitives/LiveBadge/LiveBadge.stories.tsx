import type { Meta, StoryObj } from '@storybook/react';
import { LiveBadge } from './LiveBadge';

const meta: Meta<typeof LiveBadge> = {
  title: 'Primitives/LiveBadge',
  component: LiveBadge,
};
export default meta;

type Story = StoryObj<typeof LiveBadge>;

export const Default: Story = {};
export const CustomLabel: Story = { args: { label: 'STREAMING' } };
