import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Drawer, DrawerContent, DrawerTrigger, DrawerClose } from './Drawer';

function setup() {
  return userEvent.setup();
}

describe('Drawer', () => {
  it('renders trigger; panel is not in DOM when closed', () => {
    render(
      <Drawer>
        <DrawerTrigger>Open</DrawerTrigger>
        <DrawerContent title="Test Drawer">Body</DrawerContent>
      </Drawer>,
    );
    expect(screen.getByRole('button', { name: 'Open' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens when trigger is clicked', async () => {
    const user = setup();
    render(
      <Drawer>
        <DrawerTrigger>Open drawer</DrawerTrigger>
        <DrawerContent title="Side Panel">Panel body</DrawerContent>
      </Drawer>,
    );
    await user.click(screen.getByRole('button', { name: 'Open drawer' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Side Panel')).toBeInTheDocument();
    expect(screen.getByText('Panel body')).toBeInTheDocument();
  });

  it('closes when the close button is clicked', async () => {
    const user = setup();
    render(
      <Drawer>
        <DrawerTrigger>Open</DrawerTrigger>
        <DrawerContent title="Drawer">Body</DrawerContent>
      </Drawer>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    await user.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('closes on Escape key', async () => {
    const user = setup();
    render(
      <Drawer>
        <DrawerTrigger>Open</DrawerTrigger>
        <DrawerContent title="Escape Drawer">Body</DrawerContent>
      </Drawer>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('applies right-side class by default', async () => {
    const user = setup();
    render(
      <Drawer>
        <DrawerTrigger>Open</DrawerTrigger>
        <DrawerContent title="Right Drawer">Body</DrawerContent>
      </Drawer>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(document.querySelector('.niuu-drawer-content--right')).toBeInTheDocument();
  });

  it('applies left-side class when side="left"', async () => {
    const user = setup();
    render(
      <Drawer>
        <DrawerTrigger>Open</DrawerTrigger>
        <DrawerContent title="Left Drawer" side="left">
          Body
        </DrawerContent>
      </Drawer>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(document.querySelector('.niuu-drawer-content--left')).toBeInTheDocument();
  });

  it('applies custom width via CSS variable', async () => {
    const user = setup();
    render(
      <Drawer>
        <DrawerTrigger>Open</DrawerTrigger>
        <DrawerContent title="Wide Drawer" width={480}>
          Body
        </DrawerContent>
      </Drawer>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    const content = document.querySelector('.niuu-drawer-content') as HTMLElement;
    expect(content?.style.getPropertyValue('--niuu-drawer-width')).toBe('480px');
  });

  it('renders optional description', async () => {
    const user = setup();
    render(
      <Drawer>
        <DrawerTrigger>Open</DrawerTrigger>
        <DrawerContent title="With Desc" description="Drawer description">
          Body
        </DrawerContent>
      </Drawer>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(screen.getByText('Drawer description')).toBeInTheDocument();
  });

  it('calls onOpenChange when state changes', async () => {
    const user = setup();
    const onOpenChange = vi.fn();
    render(
      <Drawer onOpenChange={onOpenChange}>
        <DrawerTrigger>Open</DrawerTrigger>
        <DrawerContent title="Controlled">Body</DrawerContent>
      </Drawer>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });

  it('can be opened in controlled mode', () => {
    render(
      <Drawer open>
        <DrawerContent title="Always Open">Body</DrawerContent>
      </Drawer>,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('DrawerClose dismisses the drawer', async () => {
    const user = setup();
    render(
      <Drawer>
        <DrawerTrigger>Open</DrawerTrigger>
        <DrawerContent title="Drawer">
          <DrawerClose>Done</DrawerClose>
        </DrawerContent>
      </Drawer>,
    );
    await user.click(screen.getByRole('button', { name: 'Open' }));
    await user.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});
