import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Popover, PopoverContent, PopoverTrigger, PopoverClose } from './Popover';

function setup() {
  return userEvent.setup();
}

describe('Popover', () => {
  it('renders trigger; content is not in DOM when closed', () => {
    render(
      <Popover>
        <PopoverTrigger>Click me</PopoverTrigger>
        <PopoverContent>Popover body</PopoverContent>
      </Popover>,
    );
    expect(screen.getByRole('button', { name: 'Click me' })).toBeInTheDocument();
    expect(screen.queryByText('Popover body')).toBeNull();
  });

  it('opens when trigger is clicked', async () => {
    const user = setup();
    render(
      <Popover>
        <PopoverTrigger>Click me</PopoverTrigger>
        <PopoverContent>Popover body</PopoverContent>
      </Popover>,
    );
    await user.click(screen.getByRole('button', { name: 'Click me' }));
    expect(screen.getByText('Popover body')).toBeInTheDocument();
  });

  it('closes when trigger is clicked again', async () => {
    const user = setup();
    render(
      <Popover>
        <PopoverTrigger>Toggle</PopoverTrigger>
        <PopoverContent>Content</PopoverContent>
      </Popover>,
    );
    await user.click(screen.getByRole('button', { name: 'Toggle' }));
    expect(screen.getByText('Content')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Toggle' }));
    await waitFor(() => expect(screen.queryByText('Content')).toBeNull());
  });

  it('closes on Escape key', async () => {
    const user = setup();
    render(
      <Popover>
        <PopoverTrigger>Open</PopoverTrigger>
        <PopoverContent>Escape test</PopoverContent>
      </Popover>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(screen.getByText('Escape test')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByText('Escape test')).toBeNull());
  });

  it('closes when clicking outside the popover', async () => {
    const user = setup();
    render(
      <div>
        <Popover>
          <PopoverTrigger>Open</PopoverTrigger>
          <PopoverContent>Outside click test</PopoverContent>
        </Popover>
        <button>Outside button</button>
      </div>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(screen.getByText('Outside click test')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Outside button' }));
    await waitFor(() => expect(screen.queryByText('Outside click test')).toBeNull());
  });

  it('calls onOpenChange when open state changes', async () => {
    const user = setup();
    const onOpenChange = vi.fn();
    render(
      <Popover onOpenChange={onOpenChange}>
        <PopoverTrigger>Open</PopoverTrigger>
        <PopoverContent>Body</PopoverContent>
      </Popover>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });

  it('can be opened in controlled mode', () => {
    render(
      <Popover open>
        <PopoverTrigger>Trigger</PopoverTrigger>
        <PopoverContent>Controlled open</PopoverContent>
      </Popover>,
    );
    expect(screen.getByText('Controlled open')).toBeInTheDocument();
  });

  it('PopoverClose closes the popover', async () => {
    const user = setup();
    render(
      <Popover>
        <PopoverTrigger>Open</PopoverTrigger>
        <PopoverContent>
          Content
          <PopoverClose>Dismiss</PopoverClose>
        </PopoverContent>
      </Popover>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    await user.click(screen.getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(screen.queryByText('Content')).toBeNull());
  });
});
