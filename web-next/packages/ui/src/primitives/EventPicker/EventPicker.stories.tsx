import type { Meta, StoryObj } from '@storybook/react';
import { useState } from 'react';
import { EventPicker } from './EventPicker';
import type { EventSpec } from '@niuulabs/domain';

const CATALOG: EventSpec[] = [
  { name: 'code.changed', schema: { file: 'string', diff: 'string' } },
  { name: 'code.requested', schema: { description: 'string' } },
  { name: 'review.requested', schema: { pr: 'string' } },
  { name: 'review.completed', schema: { pr: 'string', outcome: 'string' } },
  { name: 'qa.completed', schema: { suite: 'string', passed: 'boolean', failures: 'number' } },
  { name: 'ship.completed', schema: { version: 'string', env: 'string' } },
  { name: 'incident.opened', schema: { id: 'string', severity: 'string' } },
];

const meta: Meta<typeof EventPicker> = {
  title: 'Forms/EventPicker',
  component: EventPicker,
  parameters: { a11y: {} },
  args: {
    catalog: CATALOG,
    value: '',
    placeholder: 'Pick an event…',
  },
};

export default meta;
type Story = StoryObj<typeof EventPicker>;

export const Default: Story = {
  render: function DefaultStory(args) {
    const [value, setValue] = useState(args.value);
    return <EventPicker {...args} value={value} onChange={setValue} />;
  },
};

export const WithValue: Story = {
  render: function WithValueStory(args) {
    const [value, setValue] = useState('code.changed');
    return <EventPicker {...args} value={value} onChange={setValue} />;
  },
};

export const AllowNew: Story = {
  render: function AllowNewStory(args) {
    const [value, setValue] = useState('');
    return (
      <EventPicker
        {...args}
        value={value}
        onChange={setValue}
        allowNew
        placeholder="Pick or create an event…"
      />
    );
  },
};

export const AllowEmpty: Story = {
  render: function AllowEmptyStory(args) {
    const [value, setValue] = useState('code.changed');
    return <EventPicker {...args} value={value} onChange={setValue} allowEmpty />;
  },
};

export const AllowNewAndEmpty: Story = {
  render: function AllowNewAndEmptyStory(args) {
    const [value, setValue] = useState('review.completed');
    return (
      <EventPicker
        {...args}
        value={value}
        onChange={setValue}
        allowNew
        allowEmpty
        placeholder="Pick, create, or clear…"
      />
    );
  },
};

export const Disabled: Story = {
  render: function DisabledStory(args) {
    return <EventPicker {...args} value="code.changed" onChange={() => undefined} disabled />;
  },
};

export const EmptyCatalog: Story = {
  render: function EmptyCatalogStory() {
    const [value, setValue] = useState('');
    return (
      <EventPicker
        value={value}
        onChange={setValue}
        catalog={[]}
        allowNew
        placeholder="No events yet — type to create"
      />
    );
  },
};
