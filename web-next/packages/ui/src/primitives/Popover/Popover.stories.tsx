import type { Meta, StoryObj } from '@storybook/react';
import { Popover, PopoverContent, PopoverTrigger, PopoverClose } from './Popover';

const meta: Meta<typeof Popover> = {
  title: 'Overlays/Popover',
  component: Popover,
  parameters: { a11y: {} },
};
export default meta;

type Story = StoryObj<typeof Popover>;

export const Default: Story = {
  render: () => (
    <div style={{ padding: 'var(--space-10)', display: 'flex', justifyContent: 'center' }}>
      <Popover>
        <PopoverTrigger asChild>
          <button>Open popover</button>
        </PopoverTrigger>
        <PopoverContent>
          <p style={{ margin: 0, color: 'var(--color-text-secondary)' }}>Popover content here.</p>
        </PopoverContent>
      </Popover>
    </div>
  ),
};

export const WithClose: Story = {
  render: () => (
    <div style={{ padding: 'var(--space-10)', display: 'flex', justifyContent: 'center' }}>
      <Popover>
        <PopoverTrigger asChild>
          <button>Open popover</button>
        </PopoverTrigger>
        <PopoverContent>
          <p style={{ margin: '0 0 var(--space-3)', color: 'var(--color-text-secondary)' }}>
            Popover with explicit close button.
          </p>
          <PopoverClose asChild>
            <button>Dismiss</button>
          </PopoverClose>
        </PopoverContent>
      </Popover>
    </div>
  ),
};

export const TopSide: Story = {
  render: () => (
    <div style={{ padding: 'var(--space-10)', display: 'flex', justifyContent: 'center' }}>
      <Popover>
        <PopoverTrigger asChild>
          <button>Open above</button>
        </PopoverTrigger>
        <PopoverContent side="top">
          <p style={{ margin: 0, color: 'var(--color-text-secondary)' }}>Opens above trigger.</p>
        </PopoverContent>
      </Popover>
    </div>
  ),
};

export const WithoutTrigger: Story = {
  render: () => (
    <Popover open>
      <PopoverTrigger asChild>
        <span />
      </PopoverTrigger>
      <PopoverContent>
        <p style={{ margin: 0, color: 'var(--color-text-secondary)' }}>Always visible.</p>
      </PopoverContent>
    </Popover>
  ),
};
