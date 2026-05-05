import type { Meta, StoryObj } from '@storybook/react';
import { StatusBadge, type BadgeStatus } from './StatusBadge';

const meta: Meta<typeof StatusBadge> = {
  title: 'Composites/StatusBadge',
  component: StatusBadge,
  args: { status: 'running' },
};
export default meta;

type Story = StoryObj<typeof StatusBadge>;

export const Running: Story = { args: { status: 'running' } };
export const Active: Story = { args: { status: 'active' } };
export const Complete: Story = { args: { status: 'complete' } };
export const Merged: Story = { args: { status: 'merged' } };
export const Review: Story = { args: { status: 'review' } };
export const Queued: Story = { args: { status: 'queued' } };
export const Blocked: Story = { args: { status: 'blocked' } };
export const Failed: Story = { args: { status: 'failed' } };
export const Pending: Story = { args: { status: 'pending' } };
export const Archived: Story = { args: { status: 'archived' } };
export const Gated: Story = { args: { status: 'gated' } };
export const Pulsing: Story = { args: { status: 'running', pulse: true } };

const statuses: BadgeStatus[] = [
  'running',
  'active',
  'complete',
  'merged',
  'review',
  'queued',
  'escalated',
  'blocked',
  'pending',
  'failed',
  'archived',
  'gated',
];

export const AllStatuses: Story = {
  render: () => (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      {statuses.map((s) => (
        <StatusBadge key={s} status={s} />
      ))}
    </div>
  ),
};
