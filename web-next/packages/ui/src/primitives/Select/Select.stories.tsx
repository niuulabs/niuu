import type { Meta, StoryObj } from '@storybook/react';
import { Field } from '../Field/Field';
import { Select } from './Select';

const OPTIONS = [
  { value: 'alpha', label: 'Alpha' },
  { value: 'beta', label: 'Beta' },
  { value: 'gamma', label: 'Gamma' },
  { value: 'delta', label: 'Delta (disabled)', disabled: true },
];

const meta: Meta<typeof Select> = {
  title: 'Forms/Select',
  component: Select,
  decorators: [
    (Story) => (
      <Field label="Environment">
        <Story />
      </Field>
    ),
  ],
  args: {
    options: OPTIONS,
    placeholder: 'Select environment…',
  },
};

export default meta;
type Story = StoryObj<typeof Select>;

export const Default: Story = {};

export const WithValue: Story = {
  args: {
    value: 'beta',
  },
};

export const WithError: Story = {
  decorators: [
    (Story) => (
      <Field label="Environment" error="Selection is required">
        <Story />
      </Field>
    ),
  ],
};

export const WithHint: Story = {
  decorators: [
    (Story) => (
      <Field label="Environment" hint="Choose the deployment target">
        <Story />
      </Field>
    ),
  ],
};

export const Disabled: Story = {
  args: {
    disabled: true,
    value: 'alpha',
  },
};
