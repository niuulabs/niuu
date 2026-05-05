import type { Meta, StoryObj } from '@storybook/react';
import { ActiveCursor } from './ActiveCursor';

const meta: Meta<typeof ActiveCursor> = {
  title: 'Ravn/ActiveCursor',
  component: ActiveCursor,
};

export default meta;
type Story = StoryObj<typeof ActiveCursor>;

export const Active: Story = {
  args: { status: 'running' },
};

export const Idle: Story = {
  args: { status: 'idle' },
};

export const Stopped: Story = {
  args: { status: 'stopped' },
};

export const Failed: Story = {
  args: { status: 'failed' },
};
