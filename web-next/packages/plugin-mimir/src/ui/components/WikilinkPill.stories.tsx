import type { Meta, StoryObj } from '@storybook/react';
import { WikilinkPill } from './WikilinkPill';

const meta = {
  title: 'Mimir/WikilinkPill',
  component: WikilinkPill,
  parameters: { layout: 'centered' },
  argTypes: {
    onNavigate: { action: 'navigate' },
  },
} satisfies Meta<typeof WikilinkPill>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Resolved: Story = {
  args: {
    slug: 'arch/overview',
    broken: false,
  },
};

export const Broken: Story = {
  args: {
    slug: 'component-ravn',
    broken: true,
  },
};

export const ResolvedLongSlug: Story = {
  args: {
    slug: 'infra/k8s/deployment-patterns',
    broken: false,
  },
};
