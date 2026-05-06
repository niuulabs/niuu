import type { Meta, StoryObj } from '@storybook/react';
import { Field } from './Field';
import { Input } from '../Input/Input';
import { Textarea } from '../Textarea/Textarea';

const meta: Meta<typeof Field> = {
  title: 'Forms/Field',
  component: Field,
  args: {
    label: 'Full name',
    children: <Input placeholder="Jane Doe" />,
  },
};

export default meta;
type Story = StoryObj<typeof Field>;

export const Default: Story = {};

export const WithHint: Story = {
  args: {
    hint: 'Enter your legal full name as it appears on your ID',
  },
};

export const WithError: Story = {
  args: {
    error: 'Full name is required',
  },
};

export const WithHintAndError: Story = {
  args: {
    hint: 'Enter your legal full name',
    error: 'Full name is required',
  },
};

export const Required: Story = {
  args: {
    required: true,
  },
};

export const WithTextarea: Story = {
  args: {
    label: 'Description',
    hint: 'Describe the issue in detail',
    children: <Textarea placeholder="Enter description…" rows={4} />,
  },
};

export const WithTextareaError: Story = {
  args: {
    label: 'Description',
    error: 'Description must be at least 10 characters',
    children: <Textarea placeholder="Enter description…" rows={4} />,
  },
};
