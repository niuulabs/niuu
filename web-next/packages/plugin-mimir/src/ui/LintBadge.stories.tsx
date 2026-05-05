import type { Meta, StoryObj } from '@storybook/react';
import { LintBadge } from './LintBadge';

const meta: Meta<typeof LintBadge> = {
  title: 'Mímir/LintBadge',
  component: LintBadge,
  parameters: { layout: 'centered' },
};
export default meta;

type Story = StoryObj<typeof LintBadge>;

export const Clean: Story = {
  args: { summary: { error: 0, warn: 0, info: 0 } },
};

export const ErrorsOnly: Story = {
  args: { summary: { error: 3, warn: 0, info: 0 } },
};

export const WarningsOnly: Story = {
  args: { summary: { error: 0, warn: 5, info: 0 } },
};

export const Mixed: Story = {
  args: { summary: { error: 2, warn: 3, info: 7 } },
};

export const Small: Story = {
  args: { summary: { error: 1, warn: 2, info: 0 }, size: 'sm' },
};

export const SingleError: Story = {
  args: { summary: { error: 1, warn: 0, info: 0 } },
};
