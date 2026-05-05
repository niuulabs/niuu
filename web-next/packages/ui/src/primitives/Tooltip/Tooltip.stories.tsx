import type { Meta, StoryObj } from '@storybook/react';
import { Tooltip, TooltipProvider } from './Tooltip';

const meta: Meta<typeof Tooltip> = {
  title: 'Overlays/Tooltip',
  component: Tooltip,
  decorators: [(Story) => <TooltipProvider>{Story()}</TooltipProvider>],
  parameters: { a11y: {} },
};
export default meta;

type Story = StoryObj<typeof Tooltip>;

export const Default: Story = {
  render: () => (
    <div style={{ padding: 'var(--space-10)', display: 'flex', justifyContent: 'center' }}>
      <Tooltip content="Helpful hint">
        <button>Hover me</button>
      </Tooltip>
    </div>
  ),
};

export const RichContent: Story = {
  render: () => (
    <div style={{ padding: 'var(--space-10)', display: 'flex', justifyContent: 'center' }}>
      <Tooltip
        content={
          <span>
            <strong>Shortcut:</strong> ⌘K
          </span>
        }
      >
        <button>Open palette</button>
      </Tooltip>
    </div>
  ),
};

export const Sides: Story = {
  render: () => (
    <div
      style={{
        padding: 'var(--space-12)',
        display: 'flex',
        gap: 'var(--space-4)',
        justifyContent: 'center',
        flexWrap: 'wrap',
      }}
    >
      {(['top', 'right', 'bottom', 'left'] as const).map((side) => (
        <Tooltip key={side} content={`${side} tooltip`} side={side}>
          <button>{side}</button>
        </Tooltip>
      ))}
    </div>
  ),
};
