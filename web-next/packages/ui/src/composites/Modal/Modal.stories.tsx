import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { Modal } from './Modal';

const meta: Meta<typeof Modal> = {
  title: 'Overlays/Modal',
  component: Modal,
};
export default meta;

type Story = StoryObj<typeof Modal>;

export const Default: Story = {
  render: function DefaultStory() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button onClick={() => setOpen(true)}>Open modal</button>
        <Modal
          open={open}
          onOpenChange={setOpen}
          title="Confirm action"
          description="This action cannot be undone."
          actions={[
            { label: 'Cancel', variant: 'secondary' },
            { label: 'Confirm', variant: 'primary' },
          ]}
        >
          <p style={{ margin: 0, color: 'var(--color-text-secondary)' }}>
            Are you sure you want to continue?
          </p>
        </Modal>
      </>
    );
  },
};

export const Destructive: Story = {
  render: function DestructiveStory() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button onClick={() => setOpen(true)}>Delete item</button>
        <Modal
          open={open}
          onOpenChange={setOpen}
          title="Delete workspace"
          description="This will permanently delete the workspace and all associated data."
          actions={[
            { label: 'Cancel', variant: 'secondary' },
            { label: 'Delete', variant: 'destructive' },
          ]}
        >
          <p style={{ margin: 0, color: 'var(--color-text-secondary)' }}>
            Type &quot;delete&quot; to confirm.
          </p>
        </Modal>
      </>
    );
  },
};

export const WithForm: Story = {
  render: function WithFormStory() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button onClick={() => setOpen(true)}>Launch wizard</button>
        <Modal
          open={open}
          onOpenChange={setOpen}
          title="New deployment"
          actions={[
            { label: 'Cancel', variant: 'secondary' },
            { label: 'Deploy', variant: 'primary' },
          ]}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            <label style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>
              Target environment
              <input
                type="text"
                placeholder="production"
                style={{ display: 'block', marginTop: 'var(--space-1)', width: '100%' }}
              />
            </label>
          </div>
        </Modal>
      </>
    );
  },
};
