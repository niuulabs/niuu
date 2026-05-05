import type { Meta, StoryObj } from '@storybook/react';
import { Kbd } from './Kbd';

const meta: Meta<typeof Kbd> = {
  title: 'Primitives/Kbd',
  component: Kbd,
  args: { children: '⌘K' },
};
export default meta;

type Story = StoryObj<typeof Kbd>;

export const Default: Story = {};
export const Combo: Story = {
  render: () => (
    <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
      <Kbd>⌘</Kbd>
      <span>+</span>
      <Kbd>K</Kbd>
    </span>
  ),
};
