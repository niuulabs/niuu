import type { Meta, StoryObj } from '@storybook/react';
import { Field } from '../Field/Field';
import { Textarea } from './Textarea';

const meta: Meta<typeof Textarea> = {
  title: 'Forms/Textarea',
  component: Textarea,
  decorators: [
    (Story) => (
      <Field label="Notes">
        <Story />
      </Field>
    ),
  ],
  args: {
    placeholder: 'Enter notes…',
    rows: 4,
  },
};

export default meta;
type Story = StoryObj<typeof Textarea>;

export const Default: Story = {};

export const Filled: Story = {
  args: {
    defaultValue: 'This is some existing content in the textarea.',
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
      <Field label="Description" error="Description must be at least 10 characters">
        <Story />
      </Field>
    ),
  ],
  args: {
    defaultValue: 'Too short',
  },
};

export const Disabled: Story = {
  args: {
    disabled: true,
    defaultValue: 'Read-only content',
  },
};

export const TallRows: Story = {
  args: {
    rows: 8,
    placeholder: 'Lots of room to write…',
  },
};
