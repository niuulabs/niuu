import type { Meta, StoryObj } from '@storybook/react';
import { Field } from '../Field/Field';
import { Combobox } from './Combobox';

const FRUITS = [
  { value: 'apple', label: 'Apple' },
  { value: 'banana', label: 'Banana' },
  { value: 'cherry', label: 'Cherry' },
  { value: 'date', label: 'Date' },
  { value: 'elderberry', label: 'Elderberry' },
  { value: 'fig', label: 'Fig' },
  { value: 'grape', label: 'Grape' },
  { value: 'kiwi', label: 'Kiwi (unavailable)', disabled: true },
];

const meta: Meta<typeof Combobox> = {
  title: 'Forms/Combobox',
  component: Combobox,
  decorators: [
    (Story) => (
      <Field label="Favourite fruit">
        <Story />
      </Field>
    ),
  ],
  args: {
    options: FRUITS,
    placeholder: 'Search fruits…',
  },
};

export default meta;
type Story = StoryObj<typeof Combobox>;

export const Default: Story = {};

export const WithValue: Story = {
  args: {
    value: 'banana',
  },
};

export const WithError: Story = {
  decorators: [
    (Story) => (
      <Field label="Favourite fruit" error="Please select a fruit">
        <Story />
      </Field>
    ),
  ],
};

export const WithHint: Story = {
  decorators: [
    (Story) => (
      <Field label="Favourite fruit" hint="Type to filter the list">
        <Story />
      </Field>
    ),
  ],
};

export const Disabled: Story = {
  args: {
    disabled: true,
    value: 'apple',
  },
};
