import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Dialog, DialogContent, DialogTrigger, DialogClose } from './Dialog';

function setup() {
  return userEvent.setup();
}

describe('Dialog', () => {
  it('renders trigger; content is not in DOM when closed', () => {
    render(
      <Dialog>
        <DialogTrigger>Open</DialogTrigger>
        <DialogContent title="Test Dialog">Body</DialogContent>
      </Dialog>,
    );
    expect(screen.getByRole('button', { name: 'Open' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens when trigger is clicked', async () => {
    const user = setup();
    render(
      <Dialog>
        <DialogTrigger>Open</DialogTrigger>
        <DialogContent title="Test Dialog">Body content</DialogContent>
      </Dialog>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Test Dialog')).toBeInTheDocument();
    expect(screen.getByText('Body content')).toBeInTheDocument();
  });

  it('closes when the built-in close button is clicked', async () => {
    const user = setup();
    render(
      <Dialog>
        <DialogTrigger>Open</DialogTrigger>
        <DialogContent title="Test Dialog">Body</DialogContent>
      </Dialog>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('closes on Escape key', async () => {
    const user = setup();
    render(
      <Dialog>
        <DialogTrigger>Open</DialogTrigger>
        <DialogContent title="Escape Test">Body</DialogContent>
      </Dialog>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('renders optional description', async () => {
    const user = setup();
    render(
      <Dialog>
        <DialogTrigger>Open</DialogTrigger>
        <DialogContent title="With Desc" description="A helpful description">
          Body
        </DialogContent>
      </Dialog>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(screen.getByText('A helpful description')).toBeInTheDocument();
  });

  it('omits description element when not provided', async () => {
    const user = setup();
    render(
      <Dialog>
        <DialogTrigger>Open</DialogTrigger>
        <DialogContent title="No Desc">Body</DialogContent>
      </Dialog>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(screen.queryByRole('paragraph')).toBeNull();
  });

  it('calls onOpenChange when open state changes', async () => {
    const user = setup();
    const onOpenChange = vi.fn();
    render(
      <Dialog onOpenChange={onOpenChange}>
        <DialogTrigger>Open</DialogTrigger>
        <DialogContent title="Controlled">Body</DialogContent>
      </Dialog>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });

  it('can be opened in controlled mode', () => {
    render(
      <Dialog open>
        <DialogContent title="Controlled Open">Body</DialogContent>
      </Dialog>,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('closes on overlay click (outside content)', async () => {
    const user = setup();
    render(
      <Dialog>
        <DialogTrigger>Open</DialogTrigger>
        <DialogContent title="Overlay Click">Body</DialogContent>
      </Dialog>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    // Click the overlay (it has class niuu-dialog-overlay)
    const overlay = document.querySelector('.niuu-dialog-overlay') as HTMLElement;
    fireEvent.pointerDown(overlay, { button: 0, bubbles: true });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('DialogClose renders and dismisses the dialog', async () => {
    const user = setup();
    render(
      <Dialog>
        <DialogTrigger>Open</DialogTrigger>
        <DialogContent title="With Custom Close">
          <DialogClose>Cancel</DialogClose>
        </DialogContent>
      </Dialog>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});
