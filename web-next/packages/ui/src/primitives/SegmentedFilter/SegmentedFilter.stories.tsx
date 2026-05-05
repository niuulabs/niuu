import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { SegmentedFilter } from './SegmentedFilter';

const meta: Meta<typeof SegmentedFilter> = {
  title: 'Primitives/SegmentedFilter',
  component: SegmentedFilter,
};
export default meta;

type Story = StoryObj<typeof SegmentedFilter>;

export const WithCounts: Story = {
  render: function WithCountsStory() {
    const [value, setValue] = useState('all');
    return (
      <SegmentedFilter
        options={[
          { value: 'all', label: 'All', count: 12 },
          { value: 'ready', label: 'Ready', count: 8 },
          { value: 'blocked', label: 'Blocked', count: 4 },
        ]}
        value={value}
        onChange={setValue}
        aria-label="Filter items"
      />
    );
  },
};

export const WithoutCounts: Story = {
  render: function WithoutCountsStory() {
    const [value, setValue] = useState('day');
    return (
      <SegmentedFilter
        options={[
          { value: 'day', label: 'Day' },
          { value: 'week', label: 'Week' },
          { value: 'month', label: 'Month' },
        ]}
        value={value}
        onChange={setValue}
        aria-label="Time range"
      />
    );
  },
};

export const ManyOptions: Story = {
  render: function ManyOptionsStory() {
    const [value, setValue] = useState('all');
    return (
      <SegmentedFilter
        options={[
          { value: 'all', label: 'All', count: 42 },
          { value: 'running', label: 'Running', count: 5 },
          { value: 'queued', label: 'Queued', count: 12 },
          { value: 'failed', label: 'Failed', count: 3 },
          { value: 'done', label: 'Done', count: 22 },
        ]}
        value={value}
        onChange={setValue}
      />
    );
  },
};
