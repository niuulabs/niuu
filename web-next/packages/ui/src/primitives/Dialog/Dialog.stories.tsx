import type { Meta, StoryObj } from '@storybook/react';
import { useState } from 'react';
import { Dialog, DialogContent, DialogTrigger, DialogClose } from './Dialog';

const meta: Meta<typeof Dialog> = {
  title: 'Overlays/Dialog',
  component: Dialog,
  parameters: { a11y: {} },
};
export default meta;

type Story = StoryObj<typeof Dialog>;

export const Default: Story = {
  render: () => (
    <Dialog>
      <DialogTrigger asChild>
        <button className="niuu-btn">Open dialog</button>
      </DialogTrigger>
      <DialogContent title="Confirm action" description="This action cannot be undone.">
        <p style={{ color: 'var(--color-text-secondary)', fontSize: 'var(--text-sm)', margin: 0 }}>
          Are you sure you want to continue?
        </p>
        <div
          style={{
            display: 'flex',
            gap: 'var(--space-3)',
            marginTop: 'var(--space-5)',
            justifyContent: 'flex-end',
          }}
        >
          <DialogClose asChild>
            <button>Cancel</button>
          </DialogClose>
          <DialogClose asChild>
            <button>Confirm</button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  ),
};

export const NoDescription: Story = {
  render: () => (
    <Dialog>
      <DialogTrigger asChild>
        <button>Open (no description)</button>
      </DialogTrigger>
      <DialogContent title="Settings">
        <p style={{ color: 'var(--color-text-secondary)', margin: 0 }}>Content here.</p>
      </DialogContent>
    </Dialog>
  ),
};

export const Controlled: Story = {
  render: function ControlledStory() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button onClick={() => setOpen(true)}>Open (controlled)</button>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent title="Controlled dialog">
            <p style={{ margin: 0, color: 'var(--color-text-secondary)' }}>
              Opened and closed externally.
            </p>
          </DialogContent>
        </Dialog>
      </>
    );
  },
};

export const Nested: Story = {
  render: () => (
    <Dialog>
      <DialogTrigger asChild>
        <button>Open outer</button>
      </DialogTrigger>
      <DialogContent title="Outer dialog">
        <Dialog>
          <DialogTrigger asChild>
            <button>Open inner</button>
          </DialogTrigger>
          <DialogContent title="Inner dialog">
            <p style={{ margin: 0, color: 'var(--color-text-secondary)' }}>Nested dialog!</p>
          </DialogContent>
        </Dialog>
      </DialogContent>
    </Dialog>
  ),
};
