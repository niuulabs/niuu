import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Modal } from './Modal';

function setup() {
  return userEvent.setup();
}

describe('Modal', () => {
  it('does not render content when closed', () => {
    render(
      <Modal open={false} onOpenChange={() => {}} title="Test">
        Body
      </Modal>,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('renders content when open', () => {
    render(
      <Modal open onOpenChange={() => {}} title="My Modal">
        Modal body
      </Modal>,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('My Modal')).toBeInTheDocument();
    expect(screen.getByText('Modal body')).toBeInTheDocument();
  });

  it('renders optional description', () => {
    render(
      <Modal open onOpenChange={() => {}} title="Title" description="A description">
        Body
      </Modal>,
    );
    expect(screen.getByText('A description')).toBeInTheDocument();
  });

  it('renders action buttons', () => {
    render(
      <Modal
        open
        onOpenChange={() => {}}
        title="Actions"
        actions={[
          { label: 'Cancel', variant: 'secondary' },
          { label: 'Confirm', variant: 'primary' },
        ]}
      >
        Body
      </Modal>,
    );
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument();
  });

  it('calls action onClick handler', async () => {
    const user = setup();
    const onConfirm = vi.fn();
    render(
      <Modal
        open
        onOpenChange={() => {}}
        title="Confirm"
        actions={[{ label: 'Save', variant: 'primary', onClick: onConfirm }]}
      >
        Body
      </Modal>,
    );
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('closes modal when action with closes=true is clicked', async () => {
    const user = setup();
    const onOpenChange = vi.fn();
    render(
      <Modal
        open
        onOpenChange={onOpenChange}
        title="Close Test"
        actions={[{ label: 'Done', closes: true }]}
      >
        Body
      </Modal>,
    );
    await user.click(screen.getByRole('button', { name: 'Done' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('does not close modal when action has closes=false', async () => {
    const user = setup();
    const onOpenChange = vi.fn();
    const onClick = vi.fn();
    render(
      <Modal
        open
        onOpenChange={onOpenChange}
        title="No Close"
        actions={[{ label: 'Submit', closes: false, onClick }]}
      >
        Body
      </Modal>,
    );
    await user.click(screen.getByRole('button', { name: 'Submit' }));
    expect(onClick).toHaveBeenCalled();
    // Dialog should still be open
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('disables action button when disabled=true', () => {
    render(
      <Modal
        open
        onOpenChange={() => {}}
        title="Disabled"
        actions={[{ label: 'Go', disabled: true }]}
      >
        Body
      </Modal>,
    );
    expect(screen.getByRole('button', { name: 'Go' })).toBeDisabled();
  });

  it('renders destructive variant', () => {
    render(
      <Modal
        open
        onOpenChange={() => {}}
        title="Delete"
        actions={[{ label: 'Delete', variant: 'destructive' }]}
      >
        Are you sure?
      </Modal>,
    );
    const btn = screen.getByRole('button', { name: 'Delete' });
    expect(btn).toBeInTheDocument();
  });

  it('calls onOpenChange with false on Escape', async () => {
    const user = setup();
    const onOpenChange = vi.fn();
    render(
      <Modal open onOpenChange={onOpenChange} title="Escape">
        Body
      </Modal>,
    );
    await user.keyboard('{Escape}');
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it('closes via the built-in close button in header', async () => {
    const user = setup();
    const onOpenChange = vi.fn();
    render(
      <Modal open onOpenChange={onOpenChange} title="Close Btn">
        Body
      </Modal>,
    );
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('renders without actions section when actions is empty', () => {
    render(
      <Modal open onOpenChange={() => {}} title="No Actions" actions={[]}>
        Body
      </Modal>,
    );
    expect(screen.queryByRole('button', { name: /cancel/i })).toBeNull();
  });
});
