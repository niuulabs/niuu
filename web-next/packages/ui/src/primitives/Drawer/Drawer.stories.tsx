import type { Meta, StoryObj } from '@storybook/react';
import { Drawer, DrawerContent, DrawerTrigger, DrawerClose } from './Drawer';

const meta: Meta<typeof Drawer> = {
  title: 'Overlays/Drawer',
  component: Drawer,
  parameters: { a11y: {} },
};
export default meta;

type Story = StoryObj<typeof Drawer>;

export const RightDefault: Story = {
  render: () => (
    <Drawer>
      <DrawerTrigger asChild>
        <button>Open right drawer</button>
      </DrawerTrigger>
      <DrawerContent title="Details" description="Review and edit item details.">
        <p style={{ color: 'var(--color-text-secondary)', margin: 0 }}>Drawer content goes here.</p>
        <div style={{ marginTop: 'var(--space-4)' }}>
          <DrawerClose asChild>
            <button>Close</button>
          </DrawerClose>
        </div>
      </DrawerContent>
    </Drawer>
  ),
};

export const LeftSide: Story = {
  render: () => (
    <Drawer>
      <DrawerTrigger asChild>
        <button>Open left drawer</button>
      </DrawerTrigger>
      <DrawerContent title="Navigation" side="left">
        <p style={{ color: 'var(--color-text-secondary)', margin: 0 }}>Left-side drawer.</p>
      </DrawerContent>
    </Drawer>
  ),
};

export const WideDrawer: Story = {
  render: () => (
    <Drawer>
      <DrawerTrigger asChild>
        <button>Open wide drawer (480px)</button>
      </DrawerTrigger>
      <DrawerContent title="Wide panel" width={480}>
        <p style={{ color: 'var(--color-text-secondary)', margin: 0 }}>A wider drawer.</p>
      </DrawerContent>
    </Drawer>
  ),
};

export const WithoutTrigger: Story = {
  render: () => (
    <Drawer open>
      <DrawerContent title="Always open">
        <p style={{ color: 'var(--color-text-secondary)', margin: 0 }}>Controlled open state.</p>
      </DrawerContent>
    </Drawer>
  ),
};
