import type { Meta, StoryObj } from '@storybook/react';
import { ConnectionLegend } from './ConnectionLegend';

const meta: Meta<typeof ConnectionLegend> = {
  title: 'Observatory/Overlays/ConnectionLegend',
  component: ConnectionLegend,
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof ConnectionLegend>;

export const Default: Story = {
  render: () => (
    <div
      style={{
        width: '100%',
        height: '100vh',
        background: 'var(--color-bg-primary)',
        position: 'relative',
      }}
    >
      <ConnectionLegend />
    </div>
  ),
};
