import type { Meta, StoryObj } from '@storybook/react';
import { StateDot, type DotState } from './StateDot';

const meta: Meta<typeof StateDot> = {
  title: 'Primitives/StateDot',
  component: StateDot,
  args: { state: 'healthy' },
};
export default meta;

type Story = StoryObj<typeof StateDot>;

export const Default: Story = {};
export const Pulsing: Story = { args: { state: 'running', pulse: true } };

const states: DotState[] = [
  'healthy',
  'running',
  'observing',
  'merged',
  'attention',
  'review',
  'queued',
  'processing',
  'deciding',
  'failed',
  'degraded',
  'unknown',
  'idle',
  'archived',
];

export const AllStates: Story = {
  render: () => (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'auto 1fr',
        gap: '8px 16px',
        alignItems: 'center',
      }}
    >
      {states.map((s) => (
        <div key={s} style={{ display: 'contents' }}>
          <StateDot state={s} />
          <code style={{ fontSize: 12 }}>{s}</code>
        </div>
      ))}
    </div>
  ),
};
