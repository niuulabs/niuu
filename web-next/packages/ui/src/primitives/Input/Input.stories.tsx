import type { Meta, StoryObj } from '@storybook/react';
import { Field } from '../Field/Field';
import { Input } from './Input';

const meta: Meta<typeof Input> = {
  title: 'Forms/Input',
  component: Input,
  decorators: [
    (Story) => (
      <Field label="Label">
        <Story />
      </Field>
    ),
  ],
  args: {
    placeholder: 'Enter value…',
  },
};

export default meta;
type Story = StoryObj<typeof Input>;

export const Default: Story = {};

export const Filled: Story = {
  args: {
    defaultValue: 'Some value',
  },
};

export const Focused: Story = {
  args: {
    autoFocus: true,
  },
};

export const WithError: Story = {
  decorators: [
    (Story) => (
      <Field label="Email" error="Invalid email address">
        <Story />
      </Field>
    ),
  ],
  args: {
    type: 'email',
    defaultValue: 'not-an-email',
  },
};

export const Disabled: Story = {
  args: {
    disabled: true,
    defaultValue: 'Cannot edit this',
  },
};

export const Password: Story = {
  args: {
    type: 'password',
    placeholder: 'Enter password…',
  },
};
