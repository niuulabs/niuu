import type { Meta, StoryObj } from '@storybook/react';
import { ValidationSummary } from './ValidationSummary';

const meta: Meta<typeof ValidationSummary> = {
  title: 'Forms/ValidationSummary',
  component: ValidationSummary,
  args: {
    errors: [
      { id: 'field-name', label: 'Full name', message: 'Required' },
      { id: 'field-email', label: 'Email', message: 'Must be a valid email address' },
      { id: 'field-role', label: 'Role', message: 'Please select a role' },
    ],
  },
};

export default meta;
type Story = StoryObj<typeof ValidationSummary>;

export const Default: Story = {};

export const SingleError: Story = {
  args: {
    errors: [{ id: 'field-name', label: 'Full name', message: 'Required' }],
  },
};

export const CustomHeading: Story = {
  args: {
    heading: 'The form has errors. Please review:',
  },
};

export const Empty: Story = {
  args: {
    errors: [],
  },
};
