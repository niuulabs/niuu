import type { Meta, StoryObj } from '@storybook/react';
import { LifecycleBadge } from './LifecycleBadge';
import type { LifecycleState } from './LifecycleBadge';

const meta: Meta<typeof LifecycleBadge> = {
  title: 'Composites/LifecycleBadge',
  component: LifecycleBadge,
};
export default meta;

type Story = StoryObj<typeof LifecycleBadge>;

export const Provisioning: Story = { args: { state: 'provisioning' } };
export const Ready: Story = { args: { state: 'ready' } };
export const Running: Story = { args: { state: 'running' } };
export const Idle: Story = { args: { state: 'idle' } };
export const Terminating: Story = { args: { state: 'terminating' } };
export const Terminated: Story = { args: { state: 'terminated' } };
export const Failed: Story = { args: { state: 'failed' } };

const ALL_STATES: LifecycleState[] = [
  'provisioning',
  'ready',
  'running',
  'idle',
  'terminating',
  'terminated',
  'failed',
];

export const AllStates: Story = {
  render: () => (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {ALL_STATES.map((state) => (
        <LifecycleBadge key={state} state={state} />
      ))}
    </div>
  ),
};
