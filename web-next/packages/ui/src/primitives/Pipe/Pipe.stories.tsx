import type { Meta, StoryObj } from '@storybook/react';
import { Pipe, type PipeCell } from './Pipe';

const meta: Meta<typeof Pipe> = {
  title: 'Composites/Pipe',
  component: Pipe,
};
export default meta;

type Story = StoryObj<typeof Pipe>;

const mixedSaga: PipeCell[] = [
  { status: 'ok', label: 'Decompose (complete)' },
  { status: 'ok', label: 'Research (complete)' },
  { status: 'run', label: 'Draft (running)' },
  { status: 'pend', label: 'Review (pending)' },
  { status: 'pend', label: 'Ship (pending)' },
];

const failedSaga: PipeCell[] = [
  { status: 'ok', label: 'Setup (complete)' },
  { status: 'ok', label: 'Build (complete)' },
  { status: 'crit', label: 'Verify (failed)' },
  { status: 'pend', label: 'Deploy (pending)' },
];

const gatedSaga: PipeCell[] = [
  { status: 'ok', label: 'Plan (complete)' },
  { status: 'warn', label: 'Evaluate (review)' },
  { status: 'gate', label: 'Approval gate' },
  { status: 'pend', label: 'Execute (pending)' },
];

const allStatuses: PipeCell[] = [
  { status: 'ok', label: 'ok' },
  { status: 'run', label: 'run' },
  { status: 'warn', label: 'warn' },
  { status: 'crit', label: 'crit' },
  { status: 'gate', label: 'gate' },
  { status: 'pend', label: 'pend' },
];

export const Mixed: Story = { args: { cells: mixedSaga } };
export const Failed: Story = { args: { cells: failedSaga } };
export const Gated: Story = { args: { cells: gatedSaga } };

export const AllStatuses: Story = {
  render: () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'flex-start' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <code style={{ fontSize: 11, width: 80 }}>all states</code>
        <Pipe cells={allStatuses} />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <code style={{ fontSize: 11, width: 80 }}>mixed</code>
        <Pipe cells={mixedSaga} />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <code style={{ fontSize: 11, width: 80 }}>failed</code>
        <Pipe cells={failedSaga} />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <code style={{ fontSize: 11, width: 80 }}>gated</code>
        <Pipe cells={gatedSaga} />
      </div>
    </div>
  ),
};
