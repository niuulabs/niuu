import type { Meta, StoryObj } from '@storybook/react';
import { useState } from 'react';
import { SchemaEditor } from './SchemaEditor';
import type { SchemaEditorValue } from './SchemaEditor';

const meta: Meta<typeof SchemaEditor> = {
  title: 'Forms/SchemaEditor',
  component: SchemaEditor,
  parameters: { a11y: {} },
};

export default meta;
type Story = StoryObj<typeof SchemaEditor>;

export const Empty: Story = {
  render: function EmptyStory() {
    const [value, setValue] = useState<SchemaEditorValue>({});
    return <SchemaEditor value={value} onChange={setValue} />;
  },
};

export const WithFields: Story = {
  render: function WithFieldsStory() {
    const [value, setValue] = useState<SchemaEditorValue>({
      file: 'string',
      diff: 'string',
      lineCount: 'number',
      binary: 'boolean',
    });
    return <SchemaEditor value={value} onChange={setValue} />;
  },
};

export const AllTypes: Story = {
  render: function AllTypesStory() {
    const [value, setValue] = useState<SchemaEditorValue>({
      strField: 'string',
      numField: 'number',
      boolField: 'boolean',
      objField: 'object',
      arrField: 'array',
      anyField: 'any',
    });
    return <SchemaEditor value={value} onChange={setValue} />;
  },
};

export const Readonly: Story = {
  args: {
    value: { file: 'string', diff: 'string', lineCount: 'number' },
    readonly: true,
  },
};

export const ReadonlyEmpty: Story = {
  args: {
    value: {},
    readonly: true,
  },
};

export const Controlled: Story = {
  render: function ControlledStory() {
    const [value, setValue] = useState<SchemaEditorValue>({
      id: 'string',
      name: 'string',
    });
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <SchemaEditor value={value} onChange={setValue} />
        <pre
          style={{
            background: 'var(--color-bg-secondary)',
            padding: '12px',
            borderRadius: '6px',
            fontSize: '12px',
            color: 'var(--color-text-secondary)',
          }}
        >
          {JSON.stringify(value, null, 2)}
        </pre>
      </div>
    );
  },
};
